"""启动 Anthropic→OpenAI 协议桥（替代 LiteLLM，无额外重依赖）。"""

from __future__ import annotations

from backend.anthropic_openai_bridge import main

if __name__ == "__main__":
    main()
