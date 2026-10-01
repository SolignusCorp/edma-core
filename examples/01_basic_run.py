"""EDMA-CORE basic run — no database, no framework, one file.

  EDMA_PROVIDER_KEY=... python examples/01_basic_run.py "What is 17*24?"

Without a provider key the runtime stays honest: deterministic heuristics
run and the response is an explicit failure/pending state — never canned text.
"""
import asyncio
import sys

from edma_core import quickstart, runtime as RT


async def main() -> None:
    message = " ".join(sys.argv[1:]) or "What is 17*24?"
    snap = quickstart()  # env provider (or none) + in-memory ports
    print("[edma-core] ports:", snap)
    import os
    if not os.environ.get("EDMA_PROVIDER_KEY"):
        print("[edma-core] DIQQAT: EDMA_PROVIDER_KEY yo'q — heuristikalar ishlaydi va "
              "javob halol bo'sh/xato holat bo'ladi (soxta matn YARATILMAYDI).")

    run = RT.start_run("org-demo", message)
    view = await RT.advance_run("org-demo", run)

    print("\nstate       :", view["state"])
    print("status      :", view["status"])
    print("dop_path    :", " -> ".join(view["dop_path"]))
    print("response    :", (view.get("response") or "(empty — honest failure)")[:400])
    print("verification:", view.get("verification"))


if __name__ == "__main__":
    asyncio.run(main())
