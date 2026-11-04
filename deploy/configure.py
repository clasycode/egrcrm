"""Create server configuration without printing credentials or overwriting existing files."""
import secrets,os
from pathlib import Path
path=Path('.env')
if path.exists(): raise SystemExit('.env already exists; edit it securely, it was not overwritten.')
domain=input('Domain name (example crm.example.com): ').strip()
if not domain or '/' in domain or any(c.isspace() for c in domain): raise SystemExit('Invalid domain')
text=f'DJANGO_SECRET_KEY={secrets.token_urlsafe(64)}\nPOSTGRES_PASSWORD={secrets.token_urlsafe(48)}\nALLOWED_HOSTS={domain},localhost,127.0.0.1\nCSRF_TRUSTED_ORIGINS=https://{domain}\n'
fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w') as stream: stream.write(text)
print('Created .env. Keys were not printed. Keep this file private and outside Git.')
