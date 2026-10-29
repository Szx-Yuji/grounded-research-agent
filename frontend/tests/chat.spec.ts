import { expect, type Page, test } from "@playwright/test"

import type {
  AssistantConversation,
  AssistantMessage,
  AssistantTurn,
} from "../src/lib/assistantApi"

const timestamp = "2026-10-26T04:00:00Z"
const makeConversation = (id = "chat-1"): AssistantConversation => ({
  id,
  title: id === "chat-1" ? "产品文档讨论" : "另一段对话",
  knowledge_base_ids: [],
  use_web: false,
  summary: "",
  created_at: timestamp,
  updated_at: timestamp,
})
const makeTurn = (conversationId: string, id = "turn-1"): AssistantTurn => ({
  id,
  conversation_id: conversationId,
  status: "running",
  answer: "",
  error_message: null,
  sources: [],
  created_at: timestamp,
  updated_at: timestamp,
})
const makeMessage = (
  conversationId: string,
  content: string,
  role: "user" | "assistant",
  turnId = "turn-1",
): AssistantMessage => ({
  id: `${turnId}-${role}`,
  conversation_id: conversationId,
  role,
  content,
  turn_id: turnId,
  sources: [],
  created_at: timestamp,
})
const sse = (id: number, event: string, data: unknown) =>
  `id: ${id}\nevent: ${event}\ndata: ${JSON.stringify(data)}\n\n`

async function setup(page: Page, initial: AssistantConversation[] = []) {
  const state = {
    conversations: initial,
    messages: new Map<string, AssistantMessage[]>(),
    turns: new Map<string, AssistantTurn>(),
    sent: [] as Array<{
      conversationId: string
      content: string
      authorization?: string
    }>,
    created: [] as unknown[],
    patches: [] as Record<string, unknown>[],
    eventCursors: [] as string[],
    failSend: false,
    holdStream: false,
  }
  await page.addInitScript(() => {
    localStorage.setItem("access_token", "mock-token")
    localStorage.setItem("vite-ui-theme", "light")
  })
  await page.route("**/api/v1/users/me", (route) =>
    route.fulfill({
      json: {
        id: "user-1",
        email: "reader@example.com",
        full_name: "Reader",
        is_active: true,
        is_superuser: false,
      },
    }),
  )
  await page.route("**/api/v1/knowledge-bases/**", (route) =>
    route.fulfill({
      json: {
        data: [
          {
            id: "kb-1",
            name: "产品手册",
            description: "产品资料",
            owner_id: "user-1",
            created_at: timestamp,
          },
        ],
        count: 1,
      },
    }),
  )
  await page.route("**/api/v1/assistant/conversations**", async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname.split("/assistant/conversations")[1]
    const parts = path.split("/").filter(Boolean)
    const id = parts[0]
    if (!id) {
      if (request.method() === "POST") {
        const body = request.postDataJSON()
        state.created.push(body)
        const conversation = { ...makeConversation(), ...body }
        state.conversations.push(conversation)
        await route.fulfill({ status: 201, json: conversation })
      } else
        await route.fulfill({
          json: {
            data: state.conversations,
            count: state.conversations.length,
          },
        })
    } else if (parts.length === 1) {
      const conversation = state.conversations.find((item) => item.id === id)
      if (request.method() === "PATCH") {
        Object.assign(conversation!, request.postDataJSON())
        state.patches.push(request.postDataJSON())
        await route.fulfill({ json: conversation })
      } else if (request.method() === "DELETE") {
        state.conversations = state.conversations.filter(
          (item) => item.id !== id,
        )
        await route.fulfill({ status: 204 })
      } else await route.fulfill({ json: conversation })
    } else if (parts[1] === "messages") {
      if (request.method() === "POST") {
        if (state.failSend) {
          await route.fulfill({
            status: 503,
            json: {
              detail: "Configure DEEPSEEK_API_KEY before sending messages",
            },
          })
          return
        }
        const content = request.postDataJSON().content
        state.sent.push({
          conversationId: id,
          content,
          authorization: request.headers().authorization,
        })
        const turn = makeTurn(id, `turn-${state.sent.length}`)
        state.turns.set(turn.id, turn)
        state.messages.set(id, [
          ...(state.messages.get(id) || []),
          makeMessage(id, content, "user", turn.id),
        ])
        await route.fulfill({ status: 202, json: turn })
      } else {
        const data = state.messages.get(id) || []
        await route.fulfill({ json: { data, count: data.length } })
      }
    } else if (parts[1] === "turns") {
      if (parts[2] === "active") {
        await route.fulfill({
          json:
            [...state.turns.values()].find(
              (turn) =>
                turn.conversation_id === id &&
                ["queued", "running"].includes(turn.status),
            ) || null,
        })
      } else {
        const turn = state.turns.get(parts[2])!
        if (parts[3] === "cancel") {
          turn.status = "cancelled"
          await route.fulfill({ json: turn })
        } else if (parts[3] === "events") {
          const after = url.searchParams.get("after") || "0"
          state.eventCursors.push(after)
          if (state.holdStream) {
            const body =
              after === "0"
                ? sse(1, "agent_update", {
                    agent: "RAG",
                    action: "检索文档",
                    detail: "查找产品手册中的依据",
                  }) + sse(2, "token", { content: "正在查阅" })
                : sse(2, "token", { content: "正在查阅" }) +
                  sse(3, "token", { content: "资料" })
            await route.fulfill({ contentType: "text/event-stream", body })
          } else {
            turn.status = "succeeded"
            turn.answer =
              state.sent.length > 1
                ? "沿用上文，接着解释第二个问题。"
                : "这是第一轮回答，可以继续追问。"
            const message = makeMessage(id, turn.answer, "assistant", turn.id)
            state.messages.set(id, [...(state.messages.get(id) || []), message])
            await route.fulfill({
              contentType: "text/event-stream",
              body:
                sse(1, "token", { content: turn.answer }) +
                sse(2, "complete", { turn, message }),
            })
          }
        } else await route.fulfill({ json: turn })
      }
    } else await route.fulfill({ status: 404, json: { detail: "Not found" } })
  })
  return state
}

test("ordinary multi-turn chat uses one conversation and existing Bearer authentication", async ({
  page,
}) => {
  const state = await setup(page)
  await page.goto("/chat")
  await page.getByLabel("发送消息", { exact: true }).fill("先解释一下依赖注入")
  await page.getByRole("button", { name: "发送", exact: true }).click()
  await expect(
    page.getByText("这是第一轮回答，可以继续追问。", { exact: true }),
  ).toBeVisible()
  await page
    .getByLabel("发送消息", { exact: true })
    .fill("那它和上面的例子有什么关系？")
  await page.getByRole("button", { name: "发送", exact: true }).click()
  await expect(
    page.getByText("沿用上文，接着解释第二个问题。", { exact: true }),
  ).toBeVisible()
  expect(state.created).toHaveLength(1)
  expect(state.sent.map((item) => item.conversationId)).toEqual([
    "chat-1",
    "chat-1",
  ])
  expect(
    state.sent.every((item) => item.authorization === "Bearer mock-token"),
  ).toBe(true)
  expect(state.created[0]).toMatchObject({
    knowledge_base_ids: [],
    use_web: false,
  })
  await page.reload()
  await expect(
    page.getByText("这是第一轮回答，可以继续追问。", { exact: true }),
  ).toBeVisible()
  await expect(
    page.getByText("沿用上文，接着解释第二个问题。", { exact: true }),
  ).toBeVisible()
  await page.screenshot({
    path: "/tmp/grounded-chat-desktop.png",
    fullPage: true,
  })
  await page.setViewportSize({ width: 390, height: 844 })
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true)
  await page.screenshot({
    path: "/tmp/grounded-chat-mobile.png",
    fullPage: true,
  })
})

test("persists knowledge settings, renames and deletes a conversation", async ({
  page,
}) => {
  const conversation = makeConversation()
  const state = await setup(page, [conversation])
  await page.goto("/chat?conversation=chat-1")
  await page.getByText(/资料范围 · 0 个知识库/).click()
  await page.getByRole("checkbox", { name: "产品手册", exact: true }).click()
  await expect(page.getByText(/资料范围 · 1 个知识库/)).toBeVisible()
  expect(state.patches).toContainEqual({ knowledge_base_ids: ["kb-1"] })
  await page.reload()
  await page.getByText(/资料范围 · 1 个知识库/).click()
  await expect(
    page.getByRole("checkbox", { name: "产品手册", exact: true }),
  ).toBeChecked()
  await page
    .getByRole("button", { name: "重命名：产品文档讨论", exact: true })
    .click()
  await page.getByLabel("对话名称", { exact: true }).fill("产品持续讨论")
  await page.getByRole("button", { name: "保存名称", exact: true }).click()
  await expect(
    page.getByRole("heading", { name: "产品持续讨论", exact: true }),
  ).toBeVisible()
  await page
    .getByRole("button", { name: "删除：产品持续讨论", exact: true })
    .click()
  expect(state.conversations).toHaveLength(1)
  await page.getByRole("button", { name: "删除对话", exact: true }).click()
  await expect(page).toHaveURL(/\/chat$/)
  expect(state.conversations).toHaveLength(0)
})

test("resumes an active turn after refresh, deduplicates tokens and cancels it", async ({
  page,
}) => {
  const state = await setup(page, [makeConversation()])
  const turn = makeTurn("chat-1")
  state.turns.set(turn.id, turn)
  state.messages.set("chat-1", [
    makeMessage("chat-1", "查一下产品手册", "user"),
  ])
  state.holdStream = true
  await page.goto("/chat?conversation=chat-1")
  await expect(page.getByText("正在查阅资料", { exact: true })).toBeVisible({
    timeout: 10000,
  })
  expect(state.eventCursors).toContain("2")
  await expect(
    page.getByText("正在查阅正在查阅资料", { exact: true }),
  ).toHaveCount(0)
  await page.getByText(/查看助手进度/).click()
  await expect(page.getByText("查找产品手册中的依据")).toBeVisible()
  await page.getByRole("button", { name: "停止回复", exact: true }).click()
  await expect(
    page.getByText("本次回复已停止。你可以继续发送消息。", { exact: true }),
  ).toBeVisible()
  expect(turn.status).toBe("cancelled")
})

test("changing conversations aborts the previous stream before it can change the new chat", async ({
  page,
}) => {
  const first = makeConversation()
  const other = makeConversation("chat-2")
  const state = await setup(page, [first, other])
  const turn = makeTurn("chat-1")
  state.turns.set(turn.id, turn)
  state.messages.set("chat-1", [
    makeMessage("chat-1", "一个还没回答的问题", "user"),
  ])
  state.messages.set("chat-2", [
    makeMessage("chat-2", "另一段已保存的回答", "assistant", "other-turn"),
  ])
  let release: (() => void) | undefined
  await page.route(
    "**/assistant/conversations/chat-1/turns/turn-1/events?*",
    async (route) => {
      await new Promise<void>((resolve) => {
        release = resolve
      })
      await route
        .fulfill({
          contentType: "text/event-stream",
          body: sse(1, "token", { content: "旧对话的迟到内容" }),
        })
        .catch(() => undefined)
    },
  )
  await page.goto("/chat?conversation=chat-1")
  await expect.poll(() => Boolean(release)).toBe(true)
  await page
    .locator('button[aria-pressed="false"]')
    .filter({ hasText: "另一段对话" })
    .click()
  release?.()
  await expect(
    page.getByText("另一段已保存的回答", { exact: true }),
  ).toBeVisible()
  await expect(page.getByText("旧对话的迟到内容", { exact: true })).toHaveCount(
    0,
  )
})

test("shows missing model configuration without adding a user message", async ({
  page,
}) => {
  const state = await setup(page)
  state.failSend = true
  await page.goto("/chat")
  await page
    .getByLabel("发送消息", { exact: true })
    .fill("普通聊天也应该能说明缺少配置")
  await page.getByRole("button", { name: "发送", exact: true }).click()
  await expect(
    page.getByRole("alert").filter({
      hasText: "Configure DEEPSEEK_API_KEY before sending messages",
    }),
  ).toBeVisible()
  expect(state.messages.size).toBe(0)
  await expect(page.getByLabel("发送消息", { exact: true })).toHaveValue(
    "普通聊天也应该能说明缺少配置",
  )
})
