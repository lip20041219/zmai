"""Flask TODO App — Simple task manager.

Fixed: edit_task route now handles both GET and POST methods.
"""

from flask import Flask, request, redirect, render_template_string

app = Flask(__name__)

todos = []
next_id = 1

HOME_TEMPLATE = """
<!DOCTYPE html>
<html>
<head><title>TODO App</title></head>
<body>
  <h1>TODO List</h1>
  <form method="POST" action="/add">
    <input name="title" placeholder="New task" required>
    <button type="submit">Add</button>
  </form>
  <ul>
  {% for todo in todos %}
    <li>
      {{ todo.title }}
      <a href="/edit/{{ todo.id }}">✏️</a>
      <a href="/delete/{{ todo.id }}">❌</a>
    </li>
  {% endfor %}
  </ul>
</body>
</html>
"""

EDIT_TEMPLATE = """
<!DOCTYPE html>
<html>
<head><title>Edit TODO</title></head>
<body>
  <h1>Edit Task</h1>
  <form method="POST" action="/edit/{{ todo.id }}">
    <input name="title" value="{{ todo.title }}" required>
    <button type="submit">Save</button>
  </form>
  <a href="/">Back</a>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(HOME_TEMPLATE, todos=todos)


@app.route("/add", methods=["POST"])
def add_todo():
    title = request.form.get("title", "").strip()
    if title:
        global next_id
        todos.append({"id": next_id, "title": title})
        next_id += 1
    return redirect("/")


@app.route("/edit/<int:todo_id>", methods=["GET", "POST"])
def edit_task(todo_id):
    """Handle both displaying edit form (GET) and submitting changes (POST)."""
    todo = next((t for t in todos if t["id"] == todo_id), None)
    if todo is None:
        return "Not found", 404
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        if title:
            todo["title"] = title
        return redirect("/")
    return render_template_string(EDIT_TEMPLATE, todo=todo)


@app.route("/delete/<int:todo_id>")
def delete_task(todo_id):
    global todos
    todos = [t for t in todos if t["id"] != todo_id]
    return redirect("/")


if __name__ == "__main__":
    app.run(debug=True)
