import { describe, expect, it } from "vitest";
import { dayLabel, expectedValuePct, impliedProbability, kellyFraction, parseUtc, pct, pp } from "../index";

describe("formatters", () => {
  it("formats percentages and pp", () => {
    expect(pct(0.4859)).toBe("49%");
    expect(pct(0.4859, 1)).toBe("48.6%");
    expect(pp(7.16)).toBe("+7.2 pp");
    expect(pp(-0.8)).toBe("-0.8 pp");
    expect(pct(null)).toBe("—");
  });
  it("odds math", () => {
    expect(impliedProbability(2)).toBe(0.5);
    expect(expectedValuePct(0.52, 2.5)).toBeCloseTo(30);
    expect(kellyFraction(0.52, 2.5)).toBeCloseTo(0.2);
    expect(kellyFraction(0.3, 2)).toBe(0);
  });
  it("parses naive UTC timestamps as UTC", () => {
    const d = parseUtc("2026-09-24T10:05:00");
    expect(d?.toISOString()).toBe("2026-09-24T10:05:00.000Z");
    expect(dayLabel(new Date().toISOString())).toBe("Hoje");
  });
});
