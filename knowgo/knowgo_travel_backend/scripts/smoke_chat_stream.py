"""Manual end-to-end smoke check against the running frontend proxy (uses real models).

Run from the backend directory: .venv/Scripts/python.exe scripts/smoke_chat_stream.py
Creates an isolated test account; never prints its password/token.
"""
import asyncio
import json
import secrets
import sys
import time
from uuid import uuid4

import httpx


async def main():
    async with httpx.AsyncClient(base_url="http://127.0.0.1:3000", timeout=120) as client:
        suffix = uuid4().hex[:10]
        response = await client.post("/api/v1/users/register", json={
            "username": f"stream_check_{suffix}", "email": f"stream_check_{suffix}@example.com",
            "password": secrets.token_urlsafe(24),
        })
        response.raise_for_status()
        auth = response.json()
        auth = auth.get("data", auth)
        client.headers["Authorization"] = f"Bearer {auth['access_token']}"

        async def turn(cid, content, label):
            started = time.perf_counter()
            chunks, text, errors, done = [], [], [], None
            async with client.stream("POST", "/api/v1/messages", json={
                "conversation_id": cid, "content": content,
            }) as result:
                result.raise_for_status()
                encoding = result.headers.get("content-encoding", "identity")
                cache_control = result.headers.get("cache-control")
                async for line in result.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    event = json.loads(line[5:])
                    elapsed = round(time.perf_counter() - started, 2)
                    if event["role"] == "assistant" and event.get("content"):
                        chunks.append(elapsed)
                        text.append(event["content"])
                        if len(chunks) == 1:
                            print(json.dumps({"case": label, "first_chunk_s": elapsed}), flush=True)
                    elif event["role"] == "assistant_reset":
                        text.clear()
                    elif event["role"] == "error":
                        errors.append(event["content"])
                    elif event["role"] == "done":
                        done = event.get("code")
            history = await client.get("/api/v1/messages", params={"conversation_id": cid})
            history.raise_for_status()
            body = history.json()
            rows = body.get("data", body) if isinstance(body, dict) else body
            if isinstance(rows, dict):
                rows = rows.get("messages", [])
            saved = any(m.get("role") == "assistant" for m in rows)
            report = {"case": label, "total_s": round(time.perf_counter() - started, 2),
                      "chunks": len(chunks), "last_chunk_s": chunks[-1] if chunks else None,
                      "chunk_time_count": len(set(chunks)), "content_encoding": encoding,
                      "cache_control": cache_control,
                      "done": done, "errors": errors, "saved_assistant": saved,
                      "text_preview": "".join(text)[:140]}
            print(json.dumps(report), flush=True)
            assert done == 200 and not errors and saved, report
            return report

        async def conversation():
            result = await client.post("/api/v1/conversations", json={"title": "流式修复验证"})
            result.raise_for_status()
            body = result.json()
            return body.get("data", body)["id"]

        if "--transport-check" in sys.argv:
            for base, encoding, label in [
                ("http://127.0.0.1:8000", "gzip", "direct"),
                ("http://127.0.0.1:3000", "identity", "proxy_identity"),
                ("http://127.0.0.1:3000", "gzip", "proxy_gzip"),
            ]:
                client.base_url = base
                client.headers["Accept-Encoding"] = encoding
                await turn(await conversation(), "你好，请用三句话介绍你的能力。", label)
            return
        await turn(await conversation(), "你好，请简单介绍你的能力。", "greeting")
        cid = await conversation()
        await turn(cid, "我想去上海、天津旅行，请帮我规划行程。\n"
                   "- 出发地：北京\n- 出发日期：2026-10-25\n- 旅行天数：4天\n"
                   "- 出行人数：成人2人\n- 预算：每人3000-8000元\n"
                   "- 交通方式：高铁\n- 旅行偏好：文化探索\n- 餐饮偏好：本地小吃", "manual_planning")
        await turn(cid, "暂时不继续规划，请简单解释一下你可以提供哪些帮助。", "follow_up")


if __name__ == "__main__":
    asyncio.run(main())
