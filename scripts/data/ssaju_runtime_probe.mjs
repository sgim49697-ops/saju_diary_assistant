// ssaju_runtime_probe.mjs - 고정 ssaju build의 원국·음양력 진단을 집계값만 남기며 실행한다.

import process from "node:process";

const PILLARS = ["year", "month", "day", "hour"];

function parseDateTime(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})$/.exec(value);
  if (!match) {
    throw new Error("invalid input datetime");
  }
  return {
    year: Number(match[1]),
    month: Number(match[2]),
    day: Number(match[3]),
    hour: Number(match[4]),
    minute: Number(match[5]),
  };
}

function emptyMetric() {
  return {
    rows_compared: 0,
    row_conflicts: 0,
    field_conflicts: 0,
    field_conflicts_by_pillar: { year: 0, month: 0, day: 0, hour: 0 },
    errors: 0,
  };
}

function recordComparison(metric, actual, expected) {
  metric.rows_compared += 1;
  let rowConflict = false;
  for (const pillar of PILLARS) {
    if (actual[pillar] !== expected[pillar]) {
      rowConflict = true;
      metric.field_conflicts += 1;
      metric.field_conflicts_by_pillar[pillar] += 1;
    }
  }
  if (rowConflict) metric.row_conflicts += 1;
}

function addOneDay(date) {
  date.setUTCDate(date.getUTCDate() + 1);
}

async function readStdin() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  return Buffer.concat(chunks).toString("utf8");
}

async function main() {
  const moduleUrl = process.argv[2];
  if (!moduleUrl || !moduleUrl.startsWith("file:")) {
    throw new Error("first argument must be a file URL for the pinned ssaju build");
  }
  const payload = JSON.parse(await readStdin());
  if (!payload || !Array.isArray(payload.records)) {
    throw new Error("stdin payload must contain records array");
  }

  const { calculateSaju, lunarToSolar, solarToLunar } = await import(moduleUrl);
  if (![calculateSaju, lunarToSolar, solarToLunar].every((value) => typeof value === "function")) {
    throw new Error("pinned ssaju build does not expose the required API");
  }

  const birthDiagnostics = {
    civil_clock: emptyMetric(),
    local_mean_time_only: emptyMetric(),
    source_last_replay: emptyMetric(),
    documented_policy_hybrid: emptyMetric(),
  };
  const fixed = {
    gender: "여",
    calendar: "solar",
    timezone: "Asia/Seoul",
    now: new Date("2026-01-01T00:00:00+09:00"),
  };

  for (const record of payload.records) {
    const expected = record.pillars;
    try {
      const birth = parseDateTime(record.birth_datetime_synth);
      const civil = calculateSaju({ ...birth, ...fixed, applyLocalMeanTime: false }).pillars;
      recordComparison(birthDiagnostics.civil_clock, civil, expected);

      const meanSolar = calculateSaju({
        ...birth,
        ...fixed,
        longitude: record.birth_longitude_e,
        applyLocalMeanTime: true,
      }).pillars;
      recordComparison(birthDiagnostics.local_mean_time_only, meanSolar, expected);

      const last = parseDateTime(record.last_datetime);
      const lastPillars = calculateSaju({ ...last, ...fixed, applyLocalMeanTime: false }).pillars;
      recordComparison(birthDiagnostics.source_last_replay, lastPillars, expected);
      recordComparison(
        birthDiagnostics.documented_policy_hybrid,
        { year: civil.year, month: civil.month, day: lastPillars.day, hour: lastPillars.hour },
        expected,
      );
    } catch {
      for (const metric of Object.values(birthDiagnostics)) metric.errors += 1;
    }
  }

  const roundtrip = {
    supported_solar_dates: 0,
    conversion_throws: 0,
    wrong_roundtrips: 0,
    total_failures: 0,
    throw_examples: [],
    mismatch_examples: [],
  };
  const cursor = new Date(Date.UTC(1900, 0, 1));
  const end = Date.UTC(2099, 11, 31);
  while (cursor.getTime() <= end) {
    const year = cursor.getUTCFullYear();
    const month = cursor.getUTCMonth() + 1;
    const day = cursor.getUTCDate();
    roundtrip.supported_solar_dates += 1;
    try {
      const lunar = solarToLunar(year, month, day);
      const solar = lunarToSolar(lunar.year, lunar.month, lunar.day, lunar.isLeapMonth);
      if (solar.year !== year || solar.month !== month || solar.day !== day) {
        roundtrip.wrong_roundtrips += 1;
        if (roundtrip.mismatch_examples.length < 10) {
          roundtrip.mismatch_examples.push({ input: { year, month, day }, lunar, output: solar });
        }
      }
    } catch (error) {
      roundtrip.conversion_throws += 1;
      if (roundtrip.throw_examples.length < 10) {
        roundtrip.throw_examples.push({
          input: { year, month, day },
          error: error instanceof Error ? error.message : "unknown error",
        });
      }
    }
    addOneDay(cursor);
  }
  roundtrip.total_failures = roundtrip.conversion_throws + roundtrip.wrong_roundtrips;

  process.stdout.write(
    `${JSON.stringify({
      schema_version: "1.0.0",
      node_version: process.version,
      birth_diagnostics: birthDiagnostics,
      lunar_roundtrip: roundtrip,
    })}\n`,
  );
}

main().catch((error) => {
  process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
  process.exitCode = 1;
});
