import { expect, test } from "vitest";

import {
  AppServerClient,
  valueContainsClientId,
} from "../src/app-server-client.js";

interface FakeConnection {
  close: () => void;
  sendJson: (value: unknown) => void;
  setHandlers: (
    onMessage: (text: string) => void,
    onClose: (error?: Error) => void,
  ) => void;
}

interface InspectableClient {
  notificationBytes: number;
  notifications: Array<{
    bytes: number;
    notification: { method: string; params?: unknown };
  }>;
}

test("bounds unmatched notifications by aggregate encoded bytes", () => {
  let receive: ((text: string) => void) | undefined;
  const connection: FakeConnection = {
    close: () => {},
    sendJson: () => {},
    setHandlers: (onMessage) => {
      receive = onMessage;
    },
  };
  const ClientConstructor = AppServerClient as unknown as new (
    connection: FakeConnection,
  ) => AppServerClient;
  const client = new ClientConstructor(connection);
  const payload = "x".repeat(1024 * 1024);

  for (let index = 0; index < 12; index += 1) {
    receive?.(
      JSON.stringify({
        method: `unmatched/${index}`,
        params: { payload },
      }),
    );
  }

  const inspected = client as unknown as InspectableClient;
  expect(inspected.notificationBytes).toBeLessThanOrEqual(8 * 1024 * 1024);
  expect(inspected.notifications.length).toBeLessThan(12);
  expect(inspected.notifications.at(-1)?.notification.method).toBe(
    "unmatched/11",
  );
  expect(
    inspected.notifications.some(
      ({ notification }) => notification.method === "unmatched/0",
    ),
  ).toBe(false);
});

test("rejects an over-depth response after an early client-ID match", () => {
  const match = { clientId: "wanted", type: "userMessage" };
  let hostileTail: unknown = "leaf";
  for (let depth = 0; depth < 5_000; depth += 1) {
    hostileTail = [hostileTail];
  }

  expect(() => valueContainsClientId([match, hostileTail], "wanted"))
    .toThrow(/maximum depth of 128/);
});

test("rejects an over-node response before a late client-ID match", () => {
  const values: unknown[] = Array.from(
    { length: 250_001 },
    () => 0,
  );
  values.push({ clientId: "wanted", type: "userMessage" });

  expect(() => valueContainsClientId(values, "wanted")).toThrow(
    /maximum node count of 250000/,
  );
});

test("finds a client ID within the response limits", () => {
  const response = {
    turns: [
      {
        items: [{ clientId: "wanted", type: "userMessage" }],
      },
    ],
  };

  expect(valueContainsClientId(response, "wanted")).toBe(true);
});
