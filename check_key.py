with open('.env') as f:
    for line in f:
        if line.startswith('SUPABASE_SERVICE_KEY='):
            key = line.strip().split('=', 1)[1]
            print('Longueur:', len(key))
            print('Debut:', key[:20])
            print('Fin:', key[-20:])