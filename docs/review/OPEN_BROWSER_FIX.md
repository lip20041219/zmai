# OpenInBrowserTool Fix

## Problems Fixed

### 1. `os.startfile()` on Windows is blocking

`os.startfile(abs_path)` waits for the associated application to finish before returning. For a browser opening an HTML file, this means the Tool hangs until the browser is closed.

**Fix:** Replaced with `cmd /c start "" <path>` via subprocess, which returns immediately.

### 2. File not found returned Agent path, not absolute path

```python
# Before: Agent sees "not found: output/test.html"
result = ToolResult.err(f"not found: {path}")

# After: Agent sees actual absolute path
result = ToolResult.err(f"文件不存在: D:\Project\output\test.html")
```

### 3. No error distinction between timeout, missing browser, and open failure

```python
# Before: all errors → "failed: {e}"
result = ToolResult.err(f"failed: {e}")

# After: three separate error types
except subprocess.TimeoutExpired → "浏览器打开超时"
except FileNotFoundError → "未找到浏览器程序"
except Exception → "浏览器打开失败: {type}: {e}"
```

### 4. File existence checked BEFORE browser call

If the HTML file doesn't exist at the resolved path, the tool returns an error immediately without attempting to call the browser.
