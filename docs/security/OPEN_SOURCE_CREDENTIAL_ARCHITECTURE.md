# Open Source Credential Architecture

> ZMAI Credential System — 面向开源用户的 API Key 管理设计
> 日期：2026-07-18

---

## 目录

1. [设计哲学](#1-设计哲学)
2. [核心约束](#2-核心约束)
3. [用户模型](#3-用户模型)
4. [系统架构](#4-系统架构)
5. [凭据生命周期](#5-凭据生命周期)
6. [安全边界](#6-安全边界)
7. [错误处理策略](#7-错误处理策略)
8. [国际化准备](#8-国际化准备)

---

## 1. 设计哲学

### 1a. 三条铁律

```
铁律 1: ZMAI 绝不拥有 API Key

ZMAI 的源码、安装包、文档、测试、CI 中
绝对不允许出现任何真实 API Key。

每个用户必须提供自己的 Key。

铁律 2: 用户的 Key 只属于用户

Key 存储在本机用户的凭据文件中。
不发送到任何远程服务。
不写入日志。
不在 UI 中完整显示。

铁律 3: 失败必须可诊断

当 Key 缺失、无效、冲突时：
  1. 明确告诉用户哪里有问题
  2. 明确告诉用户怎么解决
  3. 显示当前实际使用的来源
```

### 1b. 新用户体验目标

```
第一次运行 zmai:
  ┌─────────────────────────────────────┐
  │  没有检测到可用的 AI Backend。       │
  │                                     │
  │  请选择一个 AI 提供商：              │
  │                                     │
  │    [1] DeepSeek   （推荐，免注册）   │
  │    [2] Claude     (Anthropic)       │
  │    [3] OpenAI                        │
  │    [4] Gemini     (Google)          │
  │                                     │
  │  请输入编号 [1-4]:                   │
  └─────────────────────────────────────┘

  用户选择 → 输入 Key → 自动测试：
  ┌─────────────────────────────────────┐
  │  ✓ Key 有效 (DeepSeek deepseek-chat)│
  │  已保存到当前用户的凭据存储。         │
  │                                     │
  │  现在可以运行任务了:                  │
  │    zmai "写一个 Python 脚本"         │
  └─────────────────────────────────────┘
```

### 1c. 错误体验目标

```
# 坏的体验（当前）:
$ zmai "写一个脚本"
HTTP 401: {"error": {"message": "invalid x-api-key"}}

# 好的体验（目标）:
$ zmai "写一个脚本"
  ─── Preflight Check ─────────────────
  DeepSeek: API Key 无效
  原因: 服务器返回 401 Unauthorized
  建议: 运行 `zmai auth update deepseek`
  或设置环境变量 DEEPSEEK_API_KEY
```

---

## 2. 核心约束

### 2a. 开源约束

| # | 约束 | 强制措施 |
|---|------|---------|
| 1 | 源码中无 Key | Pre-commit hook 扫描 `sk-`, `ANTHROPIC_API_KEY=`, `DEEPSEEK_API_KEY=` |
| 2 | 测试中无 Key | 使用 Mock / 测试专用占位符（不连接真实 API） |
| 3 | 文档中无 Key | CI 检查文档中的 `sk-` 模式 |
| 4 | CI 日志中无 Key | 日志过滤 / 环境变量在 CI 中设为 `${{ secrets.* }}` |
| 5 | PyPI 包中无 Key | `MANIFEST.in` / `.gitignore` 排除凭据文件 |

### 2b. 安全约束

| # | 约束 | 实现 |
|---|------|------|
| 1 | Key 不在 UI 中完整显示 | 只显示前 7 位 + `****` |
| 2 | Key 不在日志中输出 | 日志过滤 `sk-` 模式 |
| 3 | Key 不在异常消息中输出 | BackendError 脱敏 |
| 4 | Key 不在 HTTP 请求 URL 中输出 | 使用 Header |
| 5 | Key 文件权限 600 | Unix `chmod 0600` |

### 2c. 平台约束

| 平台 | 凭据存储位置 | 权限控制 |
|------|-------------|---------|
| Linux | `~/.zmai/credentials` | `chmod 0600` |
| macOS | `~/.zmai/credentials` | `chmod 0600` |
| Windows | `%USERPROFILE%\.zmai\credentials` | 目录权限 |
| WSL | `~/.zmai/credentials` | `chmod 0600` |

---

## 3. 用户模型

### 3a. 用户角色

```
┌─────────────────────────────────────────────────────┐
│                     ZMAI 用户                         │
├─────────────────────────────────────────────────────┤
│                                                     │
│  职责: 提供自己的 API Key                             │
│                                                     │
│  操作:                                                │
│    zmai auth setup         首次配置                  │
│    zmai auth update deepseek 更新 Key                │
│    zmai auth status         查看状态                 │
│    zmai auth test deepseek  测试 Key 是否有效         │
│    zmai auth remove deepseek 删除 Key                │
│                                                     │
│  不操作:                                              │
│    不修改系统环境变量（除非用户自愿）                   │
│    不共享 Key                                        │
└─────────────────────────────────────────────────────┘
```

### 3b. 多用户隔离

同一台机器上的不同系统用户：

```
用户 A (alice):
  ~alice/.zmai/credentials   ← 仅 alice 可读
  ~alice/.zmai/credentials.key

用户 B (bob):
  ~bob/.zmai/credentials     ← 仅 bob 可读
  ~bob/.zmai/credentials.key
```

互不干扰。`Path.home()` 天然隔离。

### 3c. 多 Backend 共存

单个用户可以同时配置多个 Backend：

```
~/.zmai/credentials 内容（解密后）:
{
  "active_backend": "deepseek",
  "backends": {
    "deepseek": { "api_key": "sk-xxx", ... },
    "claude":   { "api_key": "sk-ant-xxx", ... },
    "gemini":   { "api_key": "AIxxx", ... }
  }
}
```

---

## 4. 系统架构

### 4a. 组件依赖图

```
┌─────────────────────────────────────────────────────────────────────┐
│                         CredentialResolver                          │
│                                                                     │
│  唯一入口: get_status(name) → CredentialStatus                       │
│                                                                     │
│  内部:                                                               │
│    1. AuthStore     → 加密文件 ~/.zmai/credentials                   │
│    2. ConfigSource  → zmai.json / ~/.zmai/config.json                │
│    3. EnvSource     → DEEPSEEK_API_KEY (最高优先级)                  │
│                                                                     │
│  原则:                                                                │
│    - 永不修改环境变量                                                   │
│    - 永不抛出异常（错误在 status.error 中）                              │
│    - 所有消费者得到相同结果                                              │
└─────────────────────────────────────────────────────────────────────┘
          ▲                    ▲                    ▲
          │                    │                    │
  ┌───────┴───────┐  ┌────────┴────────┐  ┌───────┴──────────┐
  │   Preflight   │  │  PluginRegistry │  │  Auth CLI / UI   │
  │   (运行前检查) │  │  (配置装配)      │  │  (状态显示)       │
  └───────┬───────┘  └────────┬────────┘  └──────────────────┘
          │                    │
  ┌───────┴───────┐  ┌────────┴────────┐
  │  Runtime      │  │  DeepSeekBackend│
  │  (拒绝无 Key)  │  │  (收到装配好的   │
  │               │  │   Config)       │
  └───────────────┘  └─────────────────┘
```

### 4b. 数据流

```
用户输入 Key
  │
  ▼
zmai auth update deepseek sk-xxx
  │
  ├── 1. AuthStore.set_backend("deepseek", "sk-xxx")
  │       → 加密写入 ~/.zmai/credentials
  │       → 不修改环境变量
  │       → 不修改 Shell 配置
  │
  └── 2. (可选) 提示是否设置环境变量
          → "要设为环境变量吗？[y/N]"
          → 如果 y: 输出 "export DEEPSEEK_API_KEY=..." 让用户手动执行
  
运行时
  │
  ├── CredentialResolver.get_status("deepseek")
  │     → 读 ~/.zmai/credentials（解密）
  │     → 读 Config 文件（可选）
  │     → 读 DEEPSEEK_API_KEY（如果存在）
  │
  ├── 冲突检测
  │     → 如果 file + env 不同 → status.conflict = True
  │     → 调用方显示警告
  │
  └── 返回 CredentialStatus（含最终 Key + 来源 + 冲突）
```

### 4c. 模块职责

| 模块 | 职责 | 禁止做的事 |
|------|------|-----------|
| `CredentialResolver` | 统一凭据解析 + 冲突检测 | 修改环境变量 |
| `AuthStore` | 加密文件 I/O | 查询环境变量 |
| `Preflight` | 运行前检查 Key 有效性 | 直接读文件或 env |
| `PluginRegistry._build_config` | 用 status 装配 Backend 配置 | 自行解析凭据来源 |
| `DeepSeekBackend` | 接收装配好的 config，发送 API | 自行读取 env 或 file |
| `Auth CLI` | 用户交互（update / status / test） | 自行解析凭据（走 Resolver） |
| `Doctor` | 诊断显示 | 自行解析凭据（走 Resolver） |

---

## 5. 凭据生命周期

### 5a. 生命周期状态机

```
                         ┌──────────┐
                         │ 未配置    │
                         │ (missing) │
                         └────┬─────┘
                              │
                      zmai auth setup
                      （用户输入 Key）
                              │
                              ▼
                    ┌─────────────────┐
                    │ 已配置（待验证）  │
                    │ (pending)        │
                    └────────┬────────┘
                             │
                    zmai auth test
                    （发送最小 API 请求）
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
     ┌───────────┐  ┌───────────┐  ┌───────────┐
     │ 有效      │  │ 无效      │  │ 网络错误   │
     │ (valid)   │  │ (invalid) │  │ (network) │
     └─────┬─────┘  └─────┬─────┘  └─────┬─────┘
           │              │              │
           │        zmai auth update     │
           │        （重新输入 Key）      │
           │              │              │
           └──────────────┴──────────────┘
                      │
                      ▼
              ┌───────────┐
              │ 已验证     │
              │ (verified) │
              └─────┬─────┘
                    │
              zmai auth remove
                    │
                    ▼
              ┌──────────┐
              │ 已删除    │
              │ (missing) │
              └──────────┘
```

### 5b. 状态迁移

| 当前状态 | 操作 | 下一状态 | 副作用 |
|---------|------|---------|--------|
| missing | `setup` / `update` | pending | 写入文件 |
| pending | `test` (成功) | verified | 标记 `verified_at` |
| pending | `test` (失败) | pending | 显示错误 |
| verified | `update` | pending | 覆盖文件，需重新验证 |
| verified | `remove` | missing | 删除文件条目 |
| any | 环境变量设置 | (不变) | 运行时使用 env 而非 file |

### 5c. 存储结构

```json
{
  "version": 2,
  "active_backend": "deepseek",
  "backends": {
    "deepseek": {
      "api_key": "sk-...",
      "model": "deepseek-chat",
      "base_url": "https://api.deepseek.com/v1",
      "timeout": 120,
      "max_tokens": 4096,
      "temperature": 0.7,
      "verified_at": "2026-07-18T12:00:00Z",
      "created_at": "2026-07-18T12:00:00Z",
      "source": "credentials_file"
    }
  },
  "encryption": {
    "scheme": "xor_base64_v2",
    "key_source": "file_key_v1"
  }
}
```

---

## 6. 安全边界

### 6a. 存储安全

| 层次 | 保护措施 | 绕过条件 |
|------|---------|---------|
| 文件权限 | `chmod 0600` | 同用户进程可读 |
| 加密 | XOR + base64 | 拥有 `credentials.key` 可解密 |
| 密钥文件 | `chmod 0600` | 同用户进程可读 |

### 6b. 运行时安全

| 场景 | 保护 |
|------|------|
| Key 在进程内存中 | Python 字符串不可变，GC 后残留 |
| Key 在日志中 | `logger.info` 不包含 Key |
| Key 在异常中 | `BackendError` 构造时脱敏 |
| Key 在 core dump 中 | 依赖 OS 保护 |
| Key 在调试器中 | 调试器可读进程内存 |

### 6c. 开源安全

| 检查点 | 工具 | 触发时机 |
|--------|------|---------|
| 源码扫描 | Pre-commit hook: `grep -r 'sk-' src/` | `git commit` |
| 文档扫描 | CI: `grep -rn 'sk-' docs/` | PR |
| 测试扫描 | CI: 检查测试中无真实 Key | PR |
| PyPI 包扫描 | `MANIFEST.in` 排除凭据文件 | `poetry build` |
| CI 日志扫描 | 环境变量使用 GitHub Secrets | CI run |

---

## 7. 错误处理策略

### 7a. 用户可见错误分类

| 错误类型 | 用户消息 | 示例 |
|---------|---------|------|
| KEY_MISSING | 未检测到有效 API Key | 用户没配置任何来源 |
| KEY_INVALID | API Key 无效 | 服务器返回 401 |
| KEY_EMPTY | API Key 是空字符串 | 用户输入了空 Key |
| KEY_CONFLICT | 检测到多个不同的 API Key | file 和 env 不同 |
| NETWORK_ERROR | 无法连接到 API 服务器 | DNS / 代理问题 |
| KEY_EXPIRED | API Key 已过期 | 服务器返回 403 |
| RATE_LIMITED | 请求过于频繁 | 429 |
| CREDENTIALS_CORRUPTED | 凭据文件损坏 | 解密失败 |
| CREDENTIALS_KEY_MISMATCH | 加密密钥不匹配 | 不同机器加密的文件 |

### 7b. 错误输出规范

```
# 格式:
[错误类型] 用户可理解的描述
建议的操作

# 示例:
[KEY_MISSING] DeepSeek: 未检测到 API Key
请执行: zmai auth update deepseek
或者设置环境变量: DEEPSEEK_API_KEY

# 示例:
[KEY_CONFLICT] DeepSeek: 检测到多个不同的 API Key
当前使用: 环境变量 DEEPSEEK_API_KEY
凭据文件中的 Key 被忽略。
如需使用凭据文件中的 Key:
  unset DEEPSEEK_API_KEY

# 示例:
[CREDENTIALS_CORRUPTED] 凭据文件损坏
文件: ~/.zmai/credentials
请执行: zmai auth setup
```

### 7c. 禁止输出的内容

```
❌ HTTP 401: {"error": {"message": "invalid x-api-key"}}
❌ [BACKEND_ERROR] DeepSeek API HTTP 401: ...
❌ Traceback (most recent call last):
❌ sk-da4fd2377e9a...
❌ api_key = "sk-..."
```

---

## 8. 国际化准备

### 8a. 错误消息接口

所有用户可见消息通过一个中心化函数输出：

```python
# 最终目标：支持多语言
# 当前：中文 + 英文并存，逐步统一

def user_message(code: str, lang: str = "en", **kwargs) -> str:
    """获取用户可见消息。"""
    messages = {
        "KEY_MISSING": {
            "en": "No valid API Key detected for {provider}.",
            "zh": "未检测到 {provider} 的有效 API Key。",
        },
        "KEY_CONFLICT": {
            "en": "Multiple different API Keys detected for {provider}.",
            "zh": "检测到 {provider} 的多个不同 API Key。",
        },
    }
    return messages[code][lang].format(**kwargs)
```

### 8b. 逐步实施

| 阶段 | 范围 | 语言 |
|------|------|------|
| v1 | 错误消息 | 中文（当前不变） |
| v2 | 用户可见 UI 文本 | 中文 + 英文 |
| v3 | 所有输出 | 根据 `LANG` 环境变量切换 |

---

*本文件定义了 ZMAI Credential System 的整体架构原则。*
*具体实现细节见 `../design/CREDENTIAL_RESOLVER_DESIGN.md` 和 `CREDENTIAL_MIGRATION_PLAN.md`。*
