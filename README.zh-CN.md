# AgentEval

**扫描 · 评测 · 认证。** AgentEval 是一个 Apache-2.0 开源命令行工具：按策略即代码（policy-as-code）规则包**扫描**自托管 AI 基础设施、A2A Agent Card 与 MCP 配置，用标准试卷**评测** AI Agent，两项都通过时**签发数字签名证书**。

[English](https://github.com/pony360/AgentEval/blob/main/README.md) · [简体中文](https://github.com/pony360/AgentEval/blob/main/README.zh-CN.md)

> **状态：Alpha（0.2.0）。** 内置规则包为入门级映射，全部标记为 `verified: false`，供贵司法务/合规团队审核的起点，不构成法律意见。

## 目录

- [功能](#功能)
- [安装](#安装)
- [快速开始](#快速开始)
- [命令与退出码](#命令与退出码)
- [规则包](#规则包)
- [编写规则](#编写规则)
- [评测与认证](#评测与认证)
- [配置](#配置)
- [安全模型](#安全模型)
- [参与贡献](#参与贡献)
- [许可证](#许可证)

## 功能

| | |
|---|---|
| **合规扫描** | 在线 Kubernetes 集群或离线快照；把 vLLM、TGI、Triton、Ollama、llama.cpp、SGLang、Ray Serve、KServe 工作负载标准化为事实 |
| **策略即代码** | YAML 规则 + JSON Schema 校验；安全、默认失败（fail-closed）的条件 DSL；自动检查与人工证明两类规则 |
| **分层规则包** | 国家（美国联邦指引）、区域（加州）、行业（PCI DSS）入门规则包 |
| **报告** | JSON、SARIF（GitHub 代码扫描）、HTML；中英文输出 |
| **Agent 评测** | 脚本化多轮对话 + 工具模拟；确定性断言 + 评分细则（rubric）裁判；关键项一票否决 |
| **可插拔** | OpenAI 兼容接口、A2A Agent（v0.3 与 v1.0）、通用 Webhook、演示 Agent；离线启发式 / Anthropic / OpenAI 兼容裁判 |
| **智能体协议** | 检查 A2A Agent Card（TLS、认证、OAuth 模式、签名）与 MCP 客户端配置（明文密钥、未固定版本、下载即执行、特权容器） |
| **MCP 服务器** | `agenteval mcp serve` 把扫描、评测与证书校验作为 MCP 工具提供；文件访问限定在根目录，默认不联网 |
| **认证** | 严格门槛、Ed25519 签名 JSON、HTML/PDF 证书、可选二维码验证地址与吊销状态查询 |
| **适合 CI** | 稳定退出码；无需数据库或服务端 |

## 安装

需要 Python 3.11+。

```bash
pip install "agenteval-toolkit[kubernetes]"
agenteval version
mkdir my-ai-governance && cd my-ai-governance
agenteval init          # 把入门规则包、试卷、认证策略和示例复制到当前目录
```

PyPI 包名为 `agenteval-toolkit`，命令和 Python 导入名均为 `agenteval`。CLI 默认读取当前目录下的 `rules/`、`exams/`、`certification/`，找不到时使用安装包内置的入门内容；可通过 `--rules`、`--exams` 或[环境变量](#配置)指定。

从源码安装：`git clone https://github.com/pony360/AgentEval.git && cd AgentEval && pip install -e ".[dev,kubernetes]"`

## 快速开始

以下命令全部离线运行，使用内置的示例集群快照与演示 Agent；加 `--lang zh` 输出中文。

```bash
agenteval rules validate --lang zh
agenteval scan --snapshot examples/snapshots/sample-cluster.json -f html -o scan.html --lang zh

agenteval eval run -p cs-ecommerce-v1 -a examples/agents/demo-good.yaml -f json -o eval.json \
  --attest-asset deployment/ml-prod/llama-chat --attest-output att-eval.yaml

agenteval scan --snapshot examples/snapshots/sample-cluster.json \
  --attestations examples/attestations.yaml \
  --attestations examples/attestations-certification.yaml \
  --attestations att-eval.yaml -o scan.json --fail-on none

agenteval cert keygen --public-out issuer.pem
agenteval cert issue -s northwind-cs --scan scan.json --eval eval.json -o certificates/
agenteval cert verify certificates/AEV-*.json --public-key issuer.pem --lang zh
```

## 命令与退出码

| 退出码 | 含义 |
|---|---|
| `0` | 成功 / 门槛通过 |
| `1` | 门槛未通过：存在 `--fail-on` 及以上级别的问题、评测未通过、系统不满足认证条件、证书无效 |
| `2` | 用法或输入错误（规则文件无效、文件缺失等） |

## 规则包

| 规则包 | 层级 | 规则数 | 覆盖 |
|---|---|---|---|
| `us-federal` | 国家 | 16 | NIST AI RMF 1.0、AI 600-1、SP 800-53、SP 800-190、SP 800-218A、OMB M-25-21 |
| `us-ca` | 区域 | 3 | 机器人身份披露（B&P §17940）、CCPA 数据最小化、AB 2013 训练数据透明度 |
| `pci-dss` | 行业 | 4 | PCI DSS v4.0.1（要求 3、4、8、10） |
| `agent-protocols` | 基线 | 12 | A2A Agent Card 与 MCP 客户端配置：TLS、认证、OAuth 模式、卡片签名、明文密钥、版本固定、下载即执行、容器隔离 |

规则以英文为源语言，简体中文译文位于 `rules/*/i18n/zh.yaml`。

## 编写规则

字段说明、条件操作符、事实目录、人工证明与翻译见 **[docs/rule-schema.md](https://github.com/pony360/AgentEval/blob/main/docs/rule-schema.md)**（英文）。所有 YAML 文件首行带有 `yaml-language-server` 注释，VS Code 等编辑器可直接获得补全与校验；`agenteval schemas -o DIR` 可导出 Schema。

## 评测与认证

- 试卷格式、评分与适配器：[docs/exams.md](https://github.com/pony360/AgentEval/blob/main/docs/exams.md)
- 认证门槛、密钥、验证与吊销：[docs/certification.md](https://github.com/pony360/AgentEval/blob/main/docs/certification.md)
- A2A 评测适配器与 Agent Card 检查：[docs/a2a.md](https://github.com/pony360/AgentEval/blob/main/docs/a2a.md)
- MCP 服务器与 MCP 配置扫描：[docs/mcp.md](https://github.com/pony360/AgentEval/blob/main/docs/mcp.md)

```bash
agenteval eval run -p cs-ecommerce-v1 -a examples/agents/a2a.example.yaml   # 评测 A2A Agent
agenteval a2a card https://agents.example.com                               # 检查 Agent Card
agenteval scan --mcp-config ~/.cursor/mcp.json --lang zh                    # 扫描 MCP 配置
agenteval mcp serve --root .                                                # 作为 MCP 服务器运行
```

颁证条件：安全得分 **> 90** 且质量得分 **> 90**；无未修复的高危/严重项；无待证明项；评测无关键项失败与会话错误；证据不超过 30 天。离线启发式裁判只能获得 **DEMO** 证书。证书有效期 6 个月。

## 配置

| 变量 | 默认值 | 用途 |
|---|---|---|
| `AGENTEVAL_LANG` | `en` | 输出语言（`en` / `zh`） |
| `AGENTEVAL_RULES_DIR` / `AGENTEVAL_EXAMS_DIR` / `AGENTEVAL_CERT_DIR` | `./rules` 等 | 内容目录 |
| `AGENTEVAL_SIGNING_KEY` | `./.agenteval/signing-key.pem` | Ed25519 私钥 |
| `AGENTEVAL_VERIFY_URL` / `AGENTEVAL_STATUS_URL` | — | 验证地址 / 吊销状态地址模板（含 `{id}`） |
| `AGENTEVAL_JUDGE_MODEL` / `AGENTEVAL_JUDGE_BASE_URL` | — | LLM 裁判设置 |
| `AGENTEVAL_ANNOTATION_PREFIX` | `agenteval.io/` | Kubernetes 标签/注解前缀 |

## 安全模型

只读扫描；只记录敏感环境变量的**名称**而非值；规则条件为固定 DSL，YAML 使用 `safe_load`；缺失事实不会判定为通过；API Key 只从环境变量读取；签名私钥以 0600 权限创建且已加入 `.gitignore`。漏洞报告请见 [SECURITY.md](https://github.com/pony360/AgentEval/blob/main/SECURITY.md)。

## 参与贡献

欢迎贡献规则、试卷与翻译。请先阅读 [CONTRIBUTING.md](https://github.com/pony360/AgentEval/blob/main/CONTRIBUTING.md)；提交需按 [DCO](https://developercertificate.org/) 签名（`git commit -s`）。

## 许可证

采用 [Apache License 2.0](https://github.com/pony360/AgentEval/blob/main/LICENSE) 授权，另见 [NOTICE](https://github.com/pony360/AgentEval/blob/main/NOTICE)。NIST、PCI DSS、加州法规等名称仅用于标识规则所引用的来源，不代表其所有者的认可或关联。
