export const meta = {
  name: "replace-me",
  description: "Discover items, process them in parallel, and synthesize",
}

const discovered = await agent(
  "Discover at most 50 concrete work items. Return stable IDs and concise " +
    "descriptions.",
  {
    id: "discover",
    label: "Discover work",
    sandbox: "read-only",
    schema: {
      type: "object",
      required: ["items"],
      properties: {
        items: {
          type: "array",
          maxItems: 50,
          items: {
            type: "object",
            required: ["id", "description"],
            properties: {
              id: { type: "string" },
              description: { type: "string", maxLength: 1000 },
            },
          },
        },
      },
    },
  },
)

const results = await pipeline(
  discovered.items,
  item => agent(
    `Handle this item and return a concise result:\n${item.description}`,
    {
      id: "process",
      label: item.id,
      sandbox: "read-only",
      timeoutMs: 900000,
    },
  ),
  {
    concurrency: 4,
    key: item => item.id,
    label: "process-items",
    maxItems: 50,
  },
)

const summary = await agent(
  `Synthesize these results without repeating them:\n${JSON.stringify(results)}`,
  {
    cacheKey: results,
    id: "synthesize",
    label: "Synthesize results",
    sandbox: "read-only",
    timeoutMs: 900000,
  },
)

return { results, summary }
