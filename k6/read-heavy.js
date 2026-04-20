import http from "k6/http";
import { check } from "k6";

const baseUrl = __ENV.BASE_URL || "http://localhost:8000";
const profile = __ENV.PROFILE || "hot";
const hotItemId = __ENV.HOT_ITEM_ID || "1";
const spreadItemCount = Number(__ENV.SPREAD_ITEM_COUNT || "1000");
const rate = Number(__ENV.RATE || "200");
const duration = __ENV.DURATION || "30s";
const simulateDbMs = __ENV.SIMULATE_DB_MS || "150";
const warmup = (__ENV.WARMUP || "true").toLowerCase() === "true";
const expireMode = __ENV.EXPIRE_MODE || "natural";
const expireAt = __ENV.EXPIRE_AT || "10s";

function buildScenarios() {
  const trafficScenario = {
    executor: "constant-arrival-rate",
    rate: rate,
    timeUnit: "1s",
    duration: duration,
    preAllocatedVUs: 50,
    maxVUs: 250,
  };

  if (profile === "spread") {
    return {
      spread_keys: {
        executor: trafficScenario.executor,
        rate: trafficScenario.rate,
        timeUnit: trafficScenario.timeUnit,
        duration: trafficScenario.duration,
        preAllocatedVUs: trafficScenario.preAllocatedVUs,
        maxVUs: trafficScenario.maxVUs,
        exec: "spreadKeys",
      },
    };
  }

  const scenarios = {
    hot_key: {
      executor: trafficScenario.executor,
      rate: trafficScenario.rate,
      timeUnit: trafficScenario.timeUnit,
      duration: trafficScenario.duration,
      preAllocatedVUs: trafficScenario.preAllocatedVUs,
      maxVUs: trafficScenario.maxVUs,
      exec: "hotKey",
    },
  };

  if (expireMode === "force") {
    scenarios.expire_hot_key = {
      executor: "per-vu-iterations",
      vus: 1,
      iterations: 1,
      startTime: expireAt,
      exec: "expireHotKey",
    };
  }

  return scenarios;
}

export const options = {
  scenarios: buildScenarios(),
  thresholds: {
    http_req_failed: ["rate<0.01"],
    http_req_duration: ["p(95)<1500"],
  },
};

export function setup() {
  http.post(`${baseUrl}/admin/metrics/reset`);

  if (profile === "hot" && warmup) {
    const warmResponse = http.post(
      `${baseUrl}/admin/cache/warm/${hotItemId}?simulate_db_ms=${simulateDbMs}`,
    );
    check(warmResponse, {
      "warmup status is 200": (r) => r.status === 200,
    });
  }

  return { startedAt: Date.now() };
}

export function hotKey() {
  const response = http.get(
    `${baseUrl}/items/${hotItemId}?simulate_db_ms=${simulateDbMs}`,
    { tags: { profile: "hot" } },
  );

  check(response, {
    "status is 200": (r) => r.status === 200,
    "cache header present": (r) => ["HIT", "MISS"].includes(r.headers["X-Cache"]),
  });
}

export function spreadKeys() {
  const itemId = String(1 + ((__ITER + __VU) % spreadItemCount));
  const response = http.get(
    `${baseUrl}/items/${itemId}?simulate_db_ms=${simulateDbMs}`,
    { tags: { profile: "spread" } },
  );

  check(response, {
    "status is 200": (r) => r.status === 200,
    "cache header present": (r) => ["HIT", "MISS"].includes(r.headers["X-Cache"]),
  });
}

export function expireHotKey() {
  const response = http.post(`${baseUrl}/admin/cache/expire/${hotItemId}`, null, {
    tags: { control: "expire_hot_key" },
  });

  check(response, {
    "expire status is 200": (r) => r.status === 200,
  });
}
