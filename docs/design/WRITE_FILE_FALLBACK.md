# WriteFile Fallback Design

## Problem

`write_file` tries `Path.write_text()` once. If it fails (permission, encoding, locked file), the task fails immediately.

## Fallback Chain

```
write_file(path, content)
  │
  ├─ Attempt 1: Path.write_text()
  │  └─ Success? → ✅ done
  │
  ├─ Attempt 2: PowerShell Out-File (Windows)
  │  └─ Success? → ✅ done
  │
  ├─ Attempt 3: Python open() write
  │  └─ Success? → ✅ done
  │
  └─ All failed → return detailed error with all attempts
```

Each attempt records its error. The final error includes all 3 attempts' details.
