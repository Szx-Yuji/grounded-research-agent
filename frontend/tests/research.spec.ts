import { expect, type Page, test } from "@playwright/test"

import type { ResearchRun } from "../src/lib/researchApi"

const savedRun: ResearchRun = {
  id: "saved-run",
  query: "How does our policy compare?",
  status: "succeeded",
  knowledge_base_ids: ["policy-kb"],
  use_web: false,
  final_report: "The retention period is 30 days. [K1]",
  error_message: null,
  sub_tasks: ["Find the retention period"],
  sources: [
    {
      source_id: "K1",
      kind: "knowledge",
      title: "Retention policy",
      content: "Records are retained for 30 days.",
      document_id: "policy-doc",
      chunk_index: 0,
      url: "javascript:alert('unsafe')",
    },
  ],
  created_at: "2026-10-26T02:00:00Z",
  updated_at: "2026-10-26T02:01:00Z",
}

async function mockWorkspace(page: Page, runs: ResearchRun[] = []) {
  await page.addInitScript(() => {
    localStorage.setItem("access_token", "mock-access-token")
    localStorage.setItem("vite-ui-theme", "light")
  })
  await page.route("**/api/v1/users/me", (route) =>
    route.fulfill({
      json: {
        id: "current-user",
        email: "researcher@example.com",
        full_name: "Researcher",
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
            id: "policy-kb",
            name: "Policy handbook",
            description: "Internal retention policy",
            owner_id: "current-user",
            created_at: "2026-10-26T00:00:00Z",
          },
        ],
        count: 1,
      },
    }),
  )
  await page.route("**/api/v1/research/runs?*", (route) =>
    route.fulfill({ json: { data: runs, count: runs.length } }),
  )
}

function event(id: number, name: string, data: unknown) {
  return `id: ${id}\r\nevent: ${name}\r\ndata: ${JSON.stringify(data)}\r\n\r\n`
}

test("uses the existing login and requires a source for documents-only research", async ({
  page,
}) => {
  await mockWorkspace(page)
  await page.goto("/research")
  await expect(
    page.getByRole("heading", { name: "Follow the evidence." }),
  ).toBeVisible()
  await page
    .getByLabel("What would you like to investigate?")
    .fill("Find the retention period")
  await page.getByRole("checkbox", { name: "Search the web" }).uncheck()
  await expect(
    page.getByRole("button", { name: "Start research", exact: true }),
  ).toBeDisabled()
  await page.getByRole("checkbox", { name: "Policy handbook" }).check()
  await expect(
    page.getByRole("button", { name: "Start research", exact: true }),
  ).toBeEnabled()
  await expect(
    page.getByText("Uses only the selected knowledge bases."),
  ).toBeVisible()
})

test("creates a task with Bearer auth and shows stored results after a refresh", async ({
  page,
}) => {
  const runs: ResearchRun[] = []
  await mockWorkspace(page, runs)
  let received: unknown
  let authorization: string | undefined
  await page.route("**/api/v1/research/runs", async (route) => {
    received = route.request().postDataJSON()
    authorization = route.request().headers().authorization
    runs.push(savedRun)
    await route.fulfill({ status: 201, json: savedRun })
  })
  await page.route("**/api/v1/research/runs/saved-run", (route) =>
    route.fulfill({ json: savedRun }),
  )
  await page.goto("/research")
  await page
    .getByLabel("What would you like to investigate?")
    .fill("How does our policy compare?")
  await page.getByRole("checkbox", { name: "Search the web" }).uncheck()
  await page.getByRole("checkbox", { name: "Policy handbook" }).check()
  await page
    .getByRole("button", { name: "Start research", exact: true })
    .click()
  await expect(page).toHaveURL(/run=saved-run/)
  expect(authorization).toBe("Bearer mock-access-token")
  expect(received).toEqual({
    query: savedRun.query,
    knowledge_base_ids: ["policy-kb"],
    use_web: false,
  })
  await expect(
    page.getByText(savedRun.final_report, { exact: true }),
  ).toBeVisible()
  await page.reload()
  await expect(
    page.getByText(savedRun.final_report, { exact: true }),
  ).toBeVisible()
  await page.screenshot({
    path: "/tmp/grounded-research-desktop.png",
    fullPage: true,
  })
  await page.setViewportSize({ width: 390, height: 844 })
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true)
  await page.screenshot({
    path: "/tmp/grounded-research-mobile.png",
    fullPage: true,
  })
  await page.getByRole("tab", { name: "Sources (1)" }).click()
  await page.getByText("Retention policy", { exact: true }).click()
  await expect(
    page.getByText("Records are retained for 30 days.", { exact: true }),
  ).toBeVisible()
  await expect(
    page.getByRole("link", { name: "Open original source" }),
  ).toHaveCount(0)
})

test("reconnects after the last event and ignores repeated tokens", async ({
  page,
}) => {
  const running: ResearchRun = {
    ...savedRun,
    id: "live-run",
    status: "running",
    final_report: "",
    sources: [],
  }
  await mockWorkspace(page, [running])
  const cursors: string[] = []
  await page.route("**/api/v1/research/runs/live-run", (route) =>
    route.fulfill({ json: running }),
  )
  await page.route(
    "**/api/v1/research/runs/live-run/events?*",
    async (route) => {
      const after =
        new URL(route.request().url()).searchParams.get("after") || "0"
      cursors.push(after)
      const body =
        after === "0"
          ? event(1, "agent_update", {
              agent: "Researcher",
              action: "search",
              detail: "Reading the policy",
            }) + event(2, "report_token", { text: "First " })
          : event(2, "report_token", { text: "First " }) +
            event(3, "report_token", { text: "second" })
      await route.fulfill({ contentType: "text/event-stream", body })
    },
  )
  await page.goto("/research?run=live-run")
  await expect(page.getByText("First second", { exact: true })).toBeVisible({
    timeout: 10000,
  })
  expect(cursors).toContain("2")
  await expect(
    page.getByText("First First second", { exact: true }),
  ).toHaveCount(0)
  await page.getByRole("tab", { name: "Progress", exact: true }).click()
  await expect(page.getByText("Reading the policy")).toBeVisible()
})

test("stops a running task and deletes its persisted result after confirmation", async ({
  page,
}) => {
  const run: ResearchRun = {
    ...savedRun,
    id: "cancel-run",
    status: "running",
    final_report: "",
    sources: [],
  }
  const runs = [run]
  await mockWorkspace(page, runs)
  await page.route("**/api/v1/research/runs/cancel-run/events?*", (route) =>
    route.fulfill({
      contentType: "text/event-stream",
      body: event(1, "agent_update", { agent: "Researcher", action: "search" }),
    }),
  )
  let cancelled = false
  let deleted = false
  await page.route("**/api/v1/research/runs/cancel-run", async (route) => {
    if (route.request().method() === "DELETE") {
      deleted = true
      runs.splice(0)
      await route.fulfill({ status: 204 })
    } else await route.fulfill({ json: run })
  })
  await page.route(
    "**/api/v1/research/runs/cancel-run/cancel",
    async (route) => {
      cancelled = true
      run.status = "cancelled"
      await route.fulfill({ json: run })
    },
  )
  await page.goto("/research?run=cancel-run")
  await page.getByRole("button", { name: "Stop research", exact: true }).click()
  await expect(
    page.getByText("Research stopped. Any available results are shown below."),
  ).toBeVisible()
  expect(cancelled).toBe(true)
  await page
    .getByRole("button", { name: `Delete research: ${run.query}` })
    .click()
  expect(deleted).toBe(false)
  await page
    .getByRole("button", { name: "Delete research", exact: true })
    .click()
  await expect(
    page.getByText("Your research will be saved here.", { exact: false }),
  ).toBeVisible()
  expect(deleted).toBe(true)
  await expect(page).toHaveURL(/\/research$/)
})

test("keeps a newly selected task clear of an old delayed connection", async ({
  page,
}) => {
  const running: ResearchRun = {
    ...savedRun,
    id: "old-live",
    status: "running",
    final_report: "",
    sources: [],
  }
  await mockWorkspace(page, [running, savedRun])
  await page.route("**/api/v1/research/runs/old-live", (route) =>
    route.fulfill({ json: running }),
  )
  await page.route("**/api/v1/research/runs/saved-run", (route) =>
    route.fulfill({ json: savedRun }),
  )
  let release: (() => void) | undefined
  await page.route(
    "**/api/v1/research/runs/old-live/events?*",
    async (route) => {
      await new Promise<void>((resolve) => {
        release = resolve
      })
      await route
        .fulfill({
          contentType: "text/event-stream",
          body: event(1, "report_token", { text: "STALE REPORT" }),
        })
        .catch(() => undefined)
    },
  )
  await page.goto("/research?run=old-live")
  await expect.poll(() => Boolean(release)).toBe(true)
  await page
    .locator('button[aria-pressed="false"]')
    .filter({ hasText: savedRun.query })
    .click()
  release?.()
  await expect(page).toHaveURL(/run=saved-run/)
  await expect(
    page.getByText(savedRun.final_report, { exact: true }),
  ).toBeVisible()
  await expect(page.getByText("STALE REPORT", { exact: true })).toHaveCount(0)
})
