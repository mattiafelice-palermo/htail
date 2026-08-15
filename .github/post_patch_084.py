from pathlib import Path

p = Path('src/htail_app/core.py')
t = p.read_text()
t = t.replace(
    '                size = response.headers.get("Content-Length")\n',
    '                headers = getattr(response, "headers", {})\n                size = headers.get("Content-Length") if hasattr(headers, "get") else None\n',
)
lines = t.splitlines(keepends=True)
for i, line in enumerate(lines):
    if 'checksum_match = re.search' in line:
        lines[i] = r'                checksum_match = re.search(r"\b([0-9a-fA-F]{64})\b", checksum_text)' + '\n'
        break
else:
    raise SystemExit('checksum regex line not found')
p.write_text(''.join(lines))
