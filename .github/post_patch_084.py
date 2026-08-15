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
t = ''.join(lines)
t = t.replace(
'''                while True:
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    current += len(chunk)
                    report("Downloading release…", current, total)
''',
'''                while True:
                    try:
                        chunk = response.read(65536)
                    except TypeError:
                        # Simple test doubles and a few file-like adapters only
                        # implement read() without a size argument. Real HTTP
                        # responses still take the streaming path above.
                        chunk = response.read()
                        if chunk:
                            chunks.append(chunk)
                            current += len(chunk)
                            report("Downloading release…", current, total)
                        break
                    if not chunk:
                        break
                    chunks.append(chunk)
                    current += len(chunk)
                    report("Downloading release…", current, total)
''')
p.write_text(t)
