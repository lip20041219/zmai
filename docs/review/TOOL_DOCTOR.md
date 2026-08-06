# ZMAI Tool Doctor

Extend `zmai doctor` to check all tools and workspace setup.

## Checks Added

| Check | What it tests | Output |
|-------|--------------|--------|
| ToolRegistry | All 8 tools registered | PASS / FAIL with missing tools |
| Workspace | Workspace root exists, writable | PASS / FAIL with path |
| write_file | Write to project root | PASS / FAIL with error |
| read_file | Read back written file | PASS / FAIL with error |
| shell_exec | Execute echo command | PASS / FAIL with error |
| browser | Open HTML file | PASS / FAIL with error |

## Output Format

```
  ✅ ToolRegistry   8/8 tools registered
  ✅ Workspace      D:\Project\workspace (writable)
  ✅ write_file     D:\Project\zmai_doc_test.html (21 bytes)
  ✅ read_file      zmai_doc_test.html (read back OK)
  ✅ shell_exec     echo hello (exit 0)
  ❌ browser        not tested (no DISPLAY)
```
