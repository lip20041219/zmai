"""ZMAI Demo Site — 一个含 3 个 bug 的 Flask 网站。

初始状态（测试全部失败）:
  1. 首页 404        —— home() 缺少 @app.route('/')
  2. Users API 字段错 —— 返回 "name" 而测试期望 "user"
  3. Button 端点 405  —— 只接受 POST，但页面按钮用 GET 访问

运行: ZMAI 读取测试失败 → 定位 → 修复 → 重测 → 全部通过后停止。
"""

from flask import Flask, jsonify

app = Flask(__name__)


# ── BUG 1: 首页缺路由 → GET / 返回 404 ──
def home():
    return "<h1>Welcome ZMAI Demo</h1><a href='/button'>Button</a>"


# ── BUG 2: API 字段错误 → 测试期望 "user"，此处返回 "name" ──
@app.route("/api/users")
def users():
    return jsonify({"name": "alice"})


# ── BUG 3: 按钮端点方法错误 → 应支持 GET（页面用 <a> 访问）──
@app.route("/button", methods=["POST"])
def button():
    return "Button works", 200


if __name__ == "__main__":
    app.run(debug=True)
