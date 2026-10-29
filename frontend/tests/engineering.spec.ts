import { expect, type Page, test } from "@playwright/test"

const timestamp = "2026-10-29T03:00:00Z"
const conversation = {
  id: "engineering-chat",
  title: "工程验证",
  knowledge_base_ids: [],
  use_web: false,
  summary: "",
  created_at: timestamp,
  updated_at: timestamp,
}

async function workspace(page: Page, withAnswer = false) {
  await page.addInitScript(() => {
    localStorage.setItem("access_token", "mock-engineering-token")
    localStorage.setItem("vite-ui-theme", "light")
  })
  await page.route("**/api/v1/users/me", (route) =>
    route.fulfill({
      json: {
        id: "user-1",
        email: "engineering@example.com",
        is_active: true,
        is_superuser: false,
      },
    }),
  )
  await page.route("**/api/v1/knowledge-bases/**", (route) =>
    route.fulfill({
      json: {
        count: 6,
        data: Array.from({ length: 6 }, (_, index) => ({
          id: `kb-${index + 1}`,
          name: `知识库${index + 1}`,
          owner_id: "user-1",
          description: "Fixture",
          created_at: timestamp,
        })),
      },
    }),
  )
  await page.route("**/api/v1/research/runs?*", (route) =>
    route.fulfill({ json: { data: [], count: 0 } }),
  )
  await page.route("**/api/v1/assistant/conversations**", async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path.endsWith("/messages")) {
      const data = withAnswer
        ? [
            {
              id: "saved-user",
              conversation_id: conversation.id,
              turn_id: "saved-turn",
              role: "user",
              content: "历史问题对应的执行记录。",
              sources: [],
              created_at: timestamp,
            },
            {
              id: "saved-assistant",
              conversation_id: conversation.id,
              turn_id: "saved-turn",
              role: "assistant",
              content: "有据的历史回答。",
              sources: [],
              created_at: timestamp,
            },
          ]
        : []
      await route.fulfill({ json: { data, count: data.length } })
    } else if (path.endsWith("/turns/active"))
      await route.fulfill({ json: null })
    else if (path.endsWith(`/conversations/${conversation.id}`))
      await route.fulfill({ json: conversation })
    else await route.fulfill({ json: { data: [conversation], count: 1 } })
  })
}

test("chat and research enforce the five-knowledge-base limit and chat uses 8000 characters", async ({
  page,
}) => {
  await workspace(page)
  await page.goto("/chat")
  await expect(page.getByLabel("发送消息", { exact: true })).toHaveAttribute(
    "maxlength",
    "8000",
  )
  await page.getByText(/资料范围 · 0 个知识库/).click()
  for (let number = 1; number <= 5; number += 1)
    await page
      .getByRole("checkbox", { name: `知识库${number}`, exact: true })
      .check()
  await expect(
    page.getByRole("checkbox", { name: "知识库6", exact: true }),
  ).toBeDisabled()
  await page.getByRole("checkbox", { name: "知识库1", exact: true }).uncheck()
  await expect(
    page.getByRole("checkbox", { name: "知识库6", exact: true }),
  ).toBeEnabled()
  await page.goto("/research")
  for (let number = 1; number <= 5; number += 1)
    await page
      .getByRole("checkbox", { name: `知识库${number}`, exact: true })
      .check()
  await expect(
    page.getByRole("checkbox", { name: "知识库6", exact: true }),
  ).toBeDisabled()
})

test("manual retry reuses the same intent key and changing the message creates another", async ({
  page,
}) => {
  await workspace(page)
  const keys: Array<string | undefined> = []
  await page.route(
    `**/assistant/conversations/${conversation.id}/messages`,
    async (route) => {
      if (route.request().method() !== "POST") {
        await route.fallback()
        return
      }
      keys.push(route.request().headers()["idempotency-key"])
      if (keys.length === 1) await route.abort("failed")
      else
        await route.fulfill({
          status: 503,
          json: { detail: "Configure model before chatting" },
        })
    },
  )
  await page.goto(`/chat?conversation=${conversation.id}`)
  await page.getByLabel("发送消息", { exact: true }).fill("同一条消息")
  await page.getByRole("button", { name: "发送", exact: true }).click()
  await expect(page.getByRole("alert")).toBeVisible()
  await page.getByRole("button", { name: "发送", exact: true }).click()
  await expect(
    page.getByRole("alert").filter({ hasText: "Configure model" }),
  ).toBeVisible()
  expect(keys[0]).toBeTruthy()
  expect(keys[1]).toBe(keys[0])
  await page.getByLabel("发送消息", { exact: true }).fill("改变了内容")
  await page.getByRole("button", { name: "发送", exact: true }).click()
  await expect.poll(() => keys.length).toBe(3)
  expect(keys[2]).not.toBe(keys[0])
})

test("execution details survive refresh and distinguish unknown usage from zero", async ({
  page,
}) => {
  await workspace(page, true)
  let reads = 0
  await page.route(
    "**/api/v1/integrations/turns/saved-turn/execution",
    async (route) => {
      reads += 1
      expect(route.request().headers().authorization).toBe(
        "Bearer mock-engineering-token",
      )
      await route.fulfill({
        json: {
          job_id: "persisted-job",
          status: "succeeded",
          attempt_count: 1,
          calls_truncated: false,
          usage: {
            call_counts: { model: 1 },
            known_input_tokens: 0,
            known_output_tokens: 0,
            usage_complete: false,
            reserved_output_tokens: 4096,
          },
          calls: [
            {
              id: "model-call",
              parent_id: null,
              kind: "model",
              operation: "assistant_answer",
              model: "fixture-model",
              status: "succeeded",
              duration_ms: 1250,
              first_token_ms: 420,
              input_tokens: null,
              output_tokens: null,
              error_type: null,
            },
          ],
        },
      })
    },
  )
  await page.goto(`/chat?conversation=${conversation.id}`)
  await page.getByText("执行详情", { exact: true }).click()
  await expect(page.getByText(/部分用量未知/)).toBeVisible()
  await expect(page.getByText(/首片段 0.42 秒/)).toBeVisible()
  await page.reload()
  await page.getByText("执行详情", { exact: true }).click()
  await expect(page.getByText(/运行编号 persisted-job/)).toBeVisible()
  expect(reads).toBe(2)
})

test("422 arrays display a field-specific validation message without echoing secret input", async ({
  page,
}) => {
  await workspace(page)
  await page.route(
    `**/assistant/conversations/${conversation.id}/messages`,
    async (route) => {
      if (route.request().method() !== "POST") {
        await route.fallback()
        return
      }
      await route.fulfill({
        status: 422,
        json: {
          detail: [
            {
              loc: ["body", "content"],
              msg: "String should have at most 8000 characters",
              type: "string_too_long",
              input: "DO_NOT_RENDER_SERVER_INPUT_SECRET",
            },
          ],
        },
      })
    },
  )
  await page.goto(`/chat?conversation=${conversation.id}`)
  await page
    .getByLabel("发送消息", { exact: true })
    .fill("trigger a validation response")
  await page.getByRole("button", { name: "发送", exact: true }).click()
  await expect(
    page
      .getByRole("alert")
      .filter({ hasText: "消息：String should have at most 8000 characters" }),
  ).toBeVisible()
  await expect(
    page.getByText("DO_NOT_RENDER_SERVER_INPUT_SECRET", { exact: false }),
  ).toHaveCount(0)
})
