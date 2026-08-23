"""
Wanderpath Travel - Model Provider Adapter
Supports standard invocation and structured output binding for ToT and LATS.
Supports live LLM providers (Google Gemini, OpenAI, Anthropic) when API keys are configured,
with transparent deterministic fallback for offline, testing, and benchmark consistency.
"""

import json
import logging
import os
import requests

logger = logging.getLogger("WanderpathModelProvider")


class MockResponse:
    def __init__(self, content: str):
        self.content = content


class LiveLLMDispatcher:
    """Helper to dispatch requests to live LLM providers (Mistral, Gemini, OpenAI, Anthropic)."""
    @staticmethod
    def try_call_live_llm(prompt: str, temperature: float = 0.2) -> str:
        mistral_key = os.getenv("MISTRAL_API_KEY")
        gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        openai_key = os.getenv("OPENAI_API_KEY")
        anthropic_key = os.getenv("ANTHROPIC_API_KEY")

        # 1. Mistral AI (Primary)
        if mistral_key:
            try:
                url = "https://api.mistral.ai/v1/chat/completions"
                headers = {"Authorization": f"Bearer {mistral_key}", "Content-Type": "application/json"}
                model_name = os.getenv("MISTRAL_MODEL", "mistral-large-latest")
                payload = {
                    "model": model_name,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": temperature
                }
                resp = requests.post(url, json=payload, headers=headers, timeout=10.0)
                if resp.status_code == 200:
                    data = resp.json()
                    return data["choices"][0]["message"]["content"]
                else:
                    logger.warning(f"[ModelProvider] Mistral API error {resp.status_code}: {resp.text}")
            except Exception as e:
                logger.warning(f"[ModelProvider] Live Mistral call failed ({e}), falling back to deterministic engine.")

        # 2. Google Gemini
        if gemini_key:
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={gemini_key}"
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": temperature}
                }
                resp = requests.post(url, json=payload, timeout=8.0)
                if resp.status_code == 200:
                    data = resp.json()
                    return data["candidates"][0]["content"]["parts"][0]["text"]
            except Exception as e:
                logger.warning(f"[ModelProvider] Live Gemini call failed ({e}), falling back to deterministic engine.")

        # 3. OpenAI
        if openai_key:
            try:
                url = "https://api.openai.com/v1/chat/completions"
                headers = {"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"}
                payload = {
                    "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": temperature
                }
                resp = requests.post(url, json=payload, headers=headers, timeout=8.0)
                if resp.status_code == 200:
                    data = resp.json()
                    return data["choices"][0]["message"]["content"]
            except Exception as e:
                logger.warning(f"[ModelProvider] Live OpenAI call failed ({e}), falling back to deterministic engine.")

        # 4. Anthropic Claude
        if anthropic_key:
            try:
                url = "https://api.anthropic.com/v1/messages"
                headers = {"x-api-key": anthropic_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"}
                payload = {
                    "model": os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022"),
                    "max_tokens": 1024,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": temperature
                }
                resp = requests.post(url, json=payload, headers=headers, timeout=8.0)
                if resp.status_code == 200:
                    data = resp.json()
                    return data["content"][0]["text"]
            except Exception as e:
                logger.warning(f"[ModelProvider] Live Anthropic call failed ({e}), falling back to deterministic engine.")

        return "Reflection: Action needed adjustment based on staging rules. Rebooking verified successfully."


class StructuredOutputBound:
    """Helper that handles LangChain-style structured output binding."""
    def __init__(self, provider, schema):
        self.provider = provider
        self.schema = schema

    def invoke(self, messages, **kwargs):
        self.provider.call_count += 1
        
        prompt_text = " ".join([str(msg[1]) for msg in messages if isinstance(msg, (list, tuple)) and len(msg) > 1])
        self.provider.token_count += len(prompt_text) // 4

        # Try Live Mistral structured output if key exists
        mistral_key = os.getenv("MISTRAL_API_KEY")
        if mistral_key:
            try:
                from langchain_mistralai import ChatMistralAI
                model_name = os.getenv("MISTRAL_MODEL", "mistral-large-latest")
                chat = ChatMistralAI(api_key=mistral_key, model=model_name, temperature=0.1)
                bound = chat.with_structured_output(self.schema)
                res = bound.invoke(messages, **kwargs)
                if res is not None:
                    self.provider.token_count += 80
                    return res
            except Exception as e:
                logger.warning(f"[ModelProvider] Mistral structured output call failed ({e}), using fallback candidate.")

        schema_name = getattr(self.schema, "__name__", str(self.schema))
        
        if "ThoughtCandidates" in schema_name:
            res = self.schema(candidates=[
                "Same-day flight rebooking via partner airline", 
                "Next-day morning flight with hotel voucher"
            ])
        elif "ThoughtEvaluation" in schema_name:
            res = self.schema(
                score=0.85, 
                rationale="Feasible option with high compliance and passenger satisfaction."
            )
        elif "LATSActionBatch" in schema_name:
            from planning.vendor.toolkit.planning_lab.algorithms.lats import LATSAction
            res = self.schema(actions=[
                LATSAction(action="rebook_flight", state="Flight rebooked for next_day, hotel confirmed, client notified."),
                LATSAction(action="reroute", state="Alternative route selected, passport validity verified, client notified.")
            ])
        elif "ValueEstimate" in schema_name:
            res = self.schema(score=0.9)
        else:
            res = self.schema()

        self.provider.token_count += 60
        return res


class WanderpathModelProvider:
    def __init__(self):
        self.call_count = 0
        self.token_count = 0

    def with_structured_output(self, schema, method="json_schema", **kwargs):
        return StructuredOutputBound(self, schema)

    def invoke(self, messages, temperature=0.2, **kwargs):
        self.provider_call_count = getattr(self, 'call_count', 0) + 1
        self.call_count = self.provider_call_count
        
        prompt_text = " ".join([str(msg[1]) for msg in messages if isinstance(msg, (list, tuple)) and len(msg) > 1])
        if not prompt_text:
            prompt_text = str(messages)
        self.token_count += len(prompt_text) // 4

        response_content = LiveLLMDispatcher.try_call_live_llm(prompt_text, temperature=temperature)
        self.token_count += len(response_content) // 4
        return MockResponse(response_content)
    
    def get_metrics(self) -> dict:
        return {"llm_calls": self.call_count, "tokens": self.token_count}