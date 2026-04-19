# -*- coding: utf-8 -*-
"""
llm_providers.py — Providers for screenplay information extraction
"""

from __future__ import annotations
import os
import json
from typing import Dict, Any, Optional

import requests

class BaseProvider:
    def __init__(
        self,
        model_name: str = "gpt-4o-mini",
        temperature: float = 0.1,
        max_output_tokens: int = 2000,
    ):
        self.model_name = model_name
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens

    def complete(self, prompt: str) -> Dict[str, Any]:
        raise NotImplementedError

    @staticmethod
    def _to_json_object(maybe_json) -> Dict[str, Any]:
        if isinstance(maybe_json, dict):
            return maybe_json
        if isinstance(maybe_json, str):
            try:
                return json.loads(maybe_json)
            except Exception:
                first = maybe_json.find("{")
                last = maybe_json.rfind("}")
                if first != -1 and last != -1 and last > first:
                    candidate = maybe_json[first:last + 1]
                    try:
                        return json.loads(candidate)
                    except Exception:
                        pass
        return {
            "meta": {}, "characters": [], "mentions": [],
            "interactions": [], "relations": [], "scene_summary": {}
        }

class DummyProvider(BaseProvider):
    def complete(self, prompt: str) -> Dict[str, Any]:
        return {
            "meta": {"script_id": "demo", "chunk_id": "0", "scene_id": "SCENE_000", "language": "en", "model_notes": "dummy"},
            "characters": [
                {"canon_name": "Alice", "aliases": [], "first_appearance_scene": "SCENE_000", "description": None},
                {"canon_name": "Bob",   "aliases": [], "first_appearance_scene": "SCENE_000", "description": None}
            ],
            "mentions": [],
            "interactions": [{
                "type": "DIALOGUE_EXCHANGE",
                "src": "Alice", "dst": "Bob",
                "directional": True, "scene_id": "SCENE_000",
                "evidence": "Alice talked to Bob.",
                "char_span": [0, 20], "sentiment": "positive",
                "power_dynamics": "peer", "confidence": 0.7
            }],
            "relations": [{
                "src": "Alice", "dst": "Bob", "rel_type": "FRIEND",
                "evidence": "They are friends.", "scene_id": "SCENE_000",
                "temporal": {"since_scene": "SCENE_000", "until_scene": None},
                "confidence": 0.6
            }],
            "scene_summary": {
                "who": ["Alice", "Bob"], "where": None, "when": None,
                "what": "Alice meets Bob.", "conflicts": [], "turning_points": []
            }
        }

class OpenAIProvider(BaseProvider):
    def complete(self, prompt: str) -> Dict[str, Any]:
        from openai import OpenAI
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("Missing environment variable: OPENAI_API_KEY")
        client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=self.model_name,
            temperature=self.temperature,
            max_tokens=self.max_output_tokens,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}]
        )
        txt = resp.choices[0].message.content
        return self._to_json_object(txt)

class AzureOpenAIProvider(BaseProvider):
    def complete(self, prompt: str) -> Dict[str, Any]:
        from openai import AzureOpenAI
        api_key   = os.getenv("AZURE_OPENAI_API_KEY")
        endpoint  = os.getenv("AZURE_OPENAI_ENDPOINT")
        deploy    = os.getenv("AZURE_OPENAI_DEPLOYMENT", self.model_name)
        api_ver   = os.getenv("AZURE_OPENAI_API_VERSION", "2024-06-01")
        if not api_key or not endpoint:
            raise RuntimeError("Missing AZURE_OPENAI_API_KEY or AZURE_OPENAI_ENDPOINT")
        client = AzureOpenAI(api_key=api_key, api_version=api_ver, azure_endpoint=endpoint)
        resp = client.chat.completions.create(
            engine=deploy,
            temperature=self.temperature,
            max_tokens=self.max_output_tokens,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}]
        )
        txt = resp.choices[0].message.content
        return self._to_json_object(txt)

class HTTPProvider(BaseProvider):
    def __init__(
        self,
        base_url: str,
        api_key: Optional[str] = None,
        model: str = "qwen2.5",
        temperature: float = 0.1,
        max_output_tokens: int = 2000,
        **_
    ):
        super().__init__(model_name=model, temperature=temperature, max_output_tokens=max_output_tokens)
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def complete(self, prompt: str) -> Dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self.temperature,
            "max_tokens": self.max_output_tokens,
            "response_format": {"type": "json_object"}
        }
        url = f"{self.base_url}/chat/completions"
        resp = requests.post(url, headers=headers, json=payload, timeout=300)
        resp.raise_for_status()
        data = resp.json()
        txt = data["choices"][0]["message"]["content"]
        return self._to_json_object(txt)

def build_provider(cfg: dict) -> BaseProvider:
    model_cfg = cfg.get("model", {}) or {}
    provider = (model_cfg.get("provider") or "dummy").lower()
    if provider == "dummy":
        return DummyProvider(
            model_name=model_cfg.get("model_name", "dummy"),
            temperature=model_cfg.get("temperature", 0.1),
            max_output_tokens=model_cfg.get("max_output_tokens", 2000)
        )
    if provider == "openai":
        return OpenAIProvider(
            model_name=model_cfg.get("model_name", "gpt-4o-mini"),
            temperature=model_cfg.get("temperature", 0.1),
            max_output_tokens=model_cfg.get("max_output_tokens", 2000)
        )
    if provider == "azure":
        return AzureOpenAIProvider(
            model_name=model_cfg.get("model_name", "gpt-4o-mini"),
            temperature=model_cfg.get("temperature", 0.1),
            max_output_tokens=model_cfg.get("max_output_tokens", 2000)
        )
    if provider == "http":
        http_cfg = cfg.get("http_provider", {}) or {}
        return HTTPProvider(
            **http_cfg,
            temperature=model_cfg.get("temperature", 0.1),
            max_output_tokens=model_cfg.get("max_output_tokens", 2000)
        )
    raise ValueError(f"Unknown provider: {provider}")
