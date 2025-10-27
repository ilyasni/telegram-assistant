# Восстановление

`git checkout -- docker-compose.yml`

`git reflog` → найти ворк-коммит до поломки.

Секреты: `cp .env.example .env` и восстановить значения (или Supabase → Settings → API).

Если стоял immutable-флаг:

```
sudo chattr -i .env
```

Затем:

```
make env-check guard up-core up-app
```
