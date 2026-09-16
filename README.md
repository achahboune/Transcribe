# TranscribeAI — Orchestrateur Fly (etape 8-9)

Cette API ne fait PAS tourner Whisper. Elle :
1. Lit l'URL du tunnel Cloudflare (colonne `tunnel_url` dans Supabase, table `tunnel_config`)
2. Verifie que ton Whisper local repond (`/health`)
3. Telecharge l'audio de la video via yt-dlp
4. Envoie l'audio a ton Whisper local (qui repond immediatement avec un job_id)
5. Interroge (poll) periodiquement `/status/{job_id}` jusqu'a ce que ce soit termine —
   cela evite que le tunnel Cloudflare timeout sur les videos longues, car chaque
   appel HTTP individuel reste court
6. Renvoie le texte transcrit

## IMPORTANT : whisper_api.py doit etre en mode asynchrone (job_id + polling)

Ton `whisper_api.py` local doit avoir ete mis a jour pour repondre immediatement
avec un `job_id` au lieu d'attendre la fin de la transcription. Si ce n'est pas
encore fait, redemande le code a jour — sans cette mise a jour, les videos de
plus de quelques minutes vont echouer avec une erreur 524 (timeout du tunnel).

## Tester en local d'abord (recommande avant Fly)

```cmd
cd transcribeai-fly
copy .env.example .env
```

Edite `.env` et mets ta VRAIE `service_role` key complete (Project Settings > API > service_role).

```cmd
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Dans un AUTRE terminal (garde aussi ton whisper_api.py ET ton start_tunnel.py actifs dans deux autres terminaux) :

```cmd
curl http://localhost:8080/api/health
curl http://localhost:8080/api/whisper-status
```

`whisper-status` doit renvoyer `{"available": true, ...}` si ton tunnel + whisper_api tournent.

Puis le vrai test, avec un lien TikTok/Insta/FB/X court et public :

```cmd
curl -X POST "http://localhost:8080/api/transcribe" -H "Content-Type: application/json" -d "{\"url\": \"COLLE_UN_LIEN_ICI\", \"language\": \"fr\"}"
```

Pour les videos longues (plusieurs minutes), la commande reste bloquee plus
longtemps — c'est normal, le polling attend la fin en interne avant de repondre.

## Deployer sur Fly (une fois le test local valide)

```cmd
flyctl launch --no-deploy
```
- Choisis un nom d'app unique
- Region: cdg (ou la plus proche)
- Refuse la creation d'une base Postgres Fly (on utilise Supabase)

```cmd
flyctl secrets set SUPABASE_URL="https://pulwpdgqcopnqzesnumj.supabase.co" SUPABASE_SERVICE_KEY="TA_VRAIE_CLE_SERVICE_ROLE"

flyctl deploy
```

```cmd
flyctl status
```

Note l'URL Fly (`https://ton-app.fly.dev`), puis teste exactement les memes commandes curl que ci-dessus mais avec cette URL a la place de `localhost:8080`.

## Important : ton PC + tunnel doivent tourner pendant le test

Avant de tester `/api/transcribe` (local ou sur Fly), assure-toi que ces 2 choses tournent sur ta machine :
1. `whisper_api.py` (l'API Whisper locale, mode asynchrone avec job_id)
2. `start_tunnel.py` (le tunnel + mise a jour Supabase)

Sans ca, `/api/whisper-status` renverra `available: false` et `/api/transcribe` renverra une erreur 503 claire — c'est le comportement voulu (echec propre, pas un crash).

## Limite pratique du tunnel gratuit Cloudflare

Le tunnel "quick" (sans compte nomme) de Cloudflare n'a aucune garantie de disponibilite
et peut couper apres une longue periode d'inactivite reseau. Le mode polling contourne
le probleme des connexions HTTP longues, mais si le tunnel lui-meme tombe completement
(ex: perte de connexion internet chez toi), il faudra relancer `start_tunnel.py`.
