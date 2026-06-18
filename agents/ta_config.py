from pathlib import Path
from tradingagents.config import TradingAgentsConfig
import tradingagents.llm as _ta_llm

BASE_DIR = Path("/home/freed/autotrade")

# gpt-4o / gpt-4o-mini don't accept reasoning_effort — only o1/o3 do.
# TradingAgents passes it unconditionally for the openai provider, so patch it out.
_orig_apply = _ta_llm._apply_reasoning
def _patched_apply(provider, effort, kwargs):
    if provider == "openai":
        return
    _orig_apply(provider, effort, kwargs)
_ta_llm._apply_reasoning = _patched_apply


def get_config() -> TradingAgentsConfig:
    return TradingAgentsConfig(
        # OpenAI for now — swap to anthropic once ANTHROPIC_API_KEY is in .env (Stage 8)
        llm_provider="openai",
        deep_think_llm="gpt-4o",        # Portfolio Manager + Researchers
        quick_think_llm="gpt-4o-mini",  # Analyst agents (cheaper, faster)

        # 1 round keeps cost and runtime reasonable for daily brief
        max_debate_rounds=1,
        max_risk_discuss_rounds=1,
        max_recur_limit=30,

        # Absolute paths so cron works from any cwd
        results_dir=BASE_DIR / "data" / "morning_briefs",
        data_cache_dir=BASE_DIR / "data" / ".ta_cache",
    )
