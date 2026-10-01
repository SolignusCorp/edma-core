"""EDMA-CORE dataset generation — direct model authoring with QC.

Two stages, both DIRECT model calls (fast; the full runtime path is opt-in):
  1) the model writes USER scenarios only (topic + language balanced);
  2) one direct call per scenario writes the assistant answer.

QC contract: empty answers, diagnostic leftovers, and duplicate scenarios are
REJECTED. If the model writes nothing, you get an empty list — never template
fabrication. Meta is honest: source="llm_direct" (no fake dop_path/trace).

  python examples/03_dataset_generation.py "everyday cooking" 4
"""
import asyncio
import json
import sys

from edma_core import quickstart, ports, runtime as RT
from edma_core.components import CognitiveModel, ComponentResult
from edma_core.dataset_exec import Factory


async def main() -> None:
    topic = sys.argv[1] if len(sys.argv) > 1 else "everyday cooking"
    size = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    quickstart()
    if ports.get_provider() is None or not getattr(ports.get_provider(), "api_key", ""):
        print("NOTE: EDMA_PROVIDER_KEY yo'q — demo model (offline) ishlatiladi. "
              "Real kalit bilan haqiqiy sample'lar yoziladi.\n")
        RT.set_model(None)  # runtime model not used by Factory (it uses the provider port)

        class DemoProvider:
            """Offline demo provider: scripted scenarios + answers (NOT part of core)."""
            async def chat(self, payload):
                last = (payload.get("messages") or [{}])[-1].get("content", "")
                if "Topic area:" in last:
                    scenarios = [f"What is a simple {topic} idea for beginners #{i}?"
                                 for i in range(1, 7)]
                    return {"ok": True, "reply": json.dumps({"scenarios": scenarios}),
                            "usage": {"prompt_tokens": 20, "completion_tokens": 60,
                                      "total_tokens": 80}, "provider": "demo",
                            "attempts": [], "error": None, "byok": False}
                return {"ok": True, "reply": f"Here is a short, honest answer about {topic}.",
                        "usage": {"prompt_tokens": 30, "completion_tokens": 40,
                                  "total_tokens": 70}, "provider": "demo",
                        "attempts": [], "error": None, "byok": False}

        ports.set_provider(DemoProvider())

    factory = Factory("org-demo", topic, size, ["en"], system_prompt="You are helpful.")
    samples = []
    for _ in range(max(size * 2, size + 4)):
        if len(samples) >= size:
            break
        s = await factory.next_sample()
        if s:
            samples.append(s)
        if factory.last_error:
            print("[qc] rejected:", factory.last_error)

    print(f"\n{len(samples)} sample (QC'dan o'tdi), meta halol:\n")
    for s in samples[:3]:
        print("-", json.dumps(s["meta"], ensure_ascii=False))
        print("  user     :", s["messages"][0]["content"][:90])
        print("  assistant:", s["messages"][1]["content"][:90])


if __name__ == "__main__":
    asyncio.run(main())
