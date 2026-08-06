# ZMAI Real Project Demos

> 三个真实项目演示，展示 ZMAI SWE Agent 修复代码问题的完整过程。

---

## Demo 1: Python CSV Processor Bug Fix

**问题**: `csv_processor.py` 中的 `parse_line()` 使用简单的 `str.split(",")` 解析 CSV，
无法处理引号包裹的逗号字段。

```python
# Before: BUG
def parse_line(line: str) -> list[str]:
    return line.split(",")

# parse_line('"a,b",c') → ['"a', 'b"', 'c']  ❌
```

**ZMAI 执行过程**:
1. 读取 `csv_processor.py` — 理解现有 parse_line 实现
2. 读取 `test_csv.py` — 理解失败测试的预期行为
3. 实现正确处理带引号字段的 CSV 解析
4. 运行 `pytest` — 全部 7 个测试通过

```python
# After: FIX
def parse_line(line: str) -> list[str]:
    fields = []
    current = []
    in_quotes = False
    for char in line:
        if char == '"':
            in_quotes = not in_quotes
        elif char == ',' and not in_quotes:
            fields.append(''.join(current))
            current = []
        else:
            current.append(char)
    fields.append(''.join(current))
    return fields

# parse_line('"a,b",c') → ['a,b', 'c']  ✅
```

**验证**: 7/7 tests passed
```
test_simple_fields ........... PASSED
test_quoted_field_with_comma  PASSED
test_parse_csv ............... PASSED
test_total_sales ............. PASSED
test_total_sales_with_quoted_name PASSED
test_filter_by ............... PASSED
test_empty_line .............. PASSED
```

---

## Demo 2: Flask TODO App Bug Fix

**问题**: `app.py` 中编辑任务的 route `@app.route("/edit/<int:todo_id>", methods=["GET"])`
只接受 GET 请求，但编辑表单提交 POST 到此 URL，导致 405 Method Not Allowed。

```python
# Before: BUG — missing POST method
@app.route("/edit/<int:todo_id>", methods=["GET"])
def edit_task(todo_id):
    todo = next((t for t in todos if t["id"] == todo_id), None)
    if todo is None:
        return "Not found", 404
    return render_template_string(EDIT_TEMPLATE, todo=todo)

# POST /edit/1 → 405 Method Not Allowed  ❌
```

**ZMAI 执行过程**:
1. 读取 `app.py` — 理解路由结构和表单提交方式
2. 定位 `edit_task` route — 发现缺少 POST 方法
3. 添加 POST 处理方法，实现标题更新逻辑
4. 运行 `pytest` — 全部 6 个测试通过

```python
# After: FIX — added POST handler
@app.route("/edit/<int:todo_id>", methods=["GET", "POST"])
def edit_task(todo_id):
    todo = next((t for t in todos if t["id"] == todo_id), None)
    if todo is None:
        return "Not found", 404
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        if title:
            todo["title"] = title
        return redirect("/")
    return render_template_string(EDIT_TEMPLATE, todo=todo)

# POST /edit/1 → 200 OK, title updated  ✅
```

**验证**: 6/6 tests passed
```
test_index ................... PASSED
test_add_todo ................ PASSED
test_edit_page_loads ......... PASSED
test_edit_submit ............. PASSED
test_delete_todo ............. PASSED
test_nonexistent_todo ........ PASSED
```

---

## Demo 3: HTML Landing Page Responsive Fix

**问题**: 首页缺少响应式设计，在移动端显示异常：
1. HTML 缺少 viewport meta 标签
2. `about-text` 和 `about-image` 使用 `min-width: 400px`，移动端溢出
3. `service-cards` 在窄屏上不换行
4. 缺少任何 media queries

**ZMAI 执行过程**:
1. 读取 `index.html` — 检查结构
2. 读取 `style.css` — 识别所有响应式问题
3. 在 HTML 中添加 viewport meta 标签
4. 修复 CSS：去除固定 min-width、添加 flex-wrap、添加 media queries

```html
<!-- Before: missing viewport -->
<head>
    <title>My Landing Page</title>
    <link rel="stylesheet" href="style.css">
</head>

<!-- After: added viewport -->
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>My Landing Page</title>
    <link rel="stylesheet" href="style.css">
</head>
```

```css
/* Before: fixed width, no wrap */
.about-text {
    min-width: 400px;
}
.service-cards {
    display: flex;
    gap: 2rem;
}

/* After: responsive, wraps on mobile */
.about-text, .about-image {
    min-width: auto;
    flex: 1 1 300px;
}
.service-cards {
    display: flex;
    gap: 2rem;
    flex-wrap: wrap;
}
@media (max-width: 768px) {
    .nav-links { flex-direction: column; gap: 0.5rem; }
    .service-cards { flex-direction: column; }
    .about-content { flex-direction: column; }
}
```

**验证**: 页面在 320px-1920px 宽度下均可正常渲染。

---

## 运行演示

每个 Demo 都包含可运行的项目和测试：

```bash
# Demo 1: Python Bug Fix
cd examples/real_project_demos/python_bug_fix
python -m pytest project/test_csv.py -v

# Demo 2: Flask Bug Fix (需要 Flask)
cd examples/real_project_demos/flask_bug_fix
pip install flask pytest
python -m pytest project/test_app.py -v

# Demo 3: HTML Bug Fix
# 直接在浏览器中打开 project/index.html
# 用 Chrome DevTools 模拟移动设备查看效果
```

## 使用 ZMAI 自动修复

```bash
# 自动规划并修复
zmai --plan "修复 csv_processor.py 中 parse_line() 不能处理引号逗号字段的 bug"
zmai --plan "修复 Flask app 中编辑任务时 405 Method Not Allowed 的问题"
zmai --plan "修复首页缺少响应式设计的问题"
```
