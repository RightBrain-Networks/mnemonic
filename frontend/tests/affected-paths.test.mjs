import assert from "node:assert/strict";
import test from "node:test";
import {
  AFFECTED_PATHS_MAX_BYTES,
  AFFECTED_PATHS_MAX_ITEMS,
  AFFECTED_PATH_MAX_BYTES,
  AffectedPathsValidationError,
  affectedPathsIssue,
  parseAffectedPathsDraft,
  validAffectedPaths
} from "../lib/affected-paths.ts";

function fixedEntry(index, length) {
  const prefix = `scope${String(index).padStart(2, "0")}/`;
  return prefix + "x".repeat(length - prefix.length);
}

test("affected-path validation preserves every valid byte, spelling, and position", () => {
  const paths = [
    "README.md",
    "src/**",
    "tests/test_*.py",
    "component/*",
    "**",
    "-leading-option-like-name",
    "A_Z/@module+name=file,part~one"
  ];
  assert.equal(validAffectedPaths(paths), true);
  assert.deepEqual(parseAffectedPathsDraft(paths.join("\n")), paths);
  assert.deepEqual(parseAffectedPathsDraft(""), []);
  assert.equal(validAffectedPaths(["Src/**", "src/**"]), true);
});

test("affected-path validation rejects the exact unsafe grammar families by index", () => {
  const invalid = [
    "",
    "/absolute",
    "trailing/",
    "double//slash",
    ".",
    "..",
    "src/./file",
    "src/../file",
    "C:/drive",
    "\\\\server\\share",
    "with space",
    "unicodé",
    "line\rreturn",
    "question?",
    "[class]",
    "{brace}",
    "quote'",
    "double\"quote",
    "$variable",
    "`command`",
    "colon:value",
    "bang!",
    "caret^",
    ":(glob)raw-magic",
    "a**b",
    "***",
    "dir/***",
    "glob/**tail"
  ];
  for (const path of invalid) {
    const issue = affectedPathsIssue(["safe", path]);
    assert.equal(issue?.index, 1, path);
    assert.match(issue?.message ?? "", /Affected path 2:/, path);
  }
  assert.throws(
    () => parseAffectedPathsDraft("safe\n\nsecond"),
    (error) => error instanceof AffectedPathsValidationError && error.index === 1
  );
  assert.throws(() => parseAffectedPathsDraft("safe\n"), AffectedPathsValidationError);
});

test("affected-path count, entry-byte, duplicate, and aggregate bounds are exact", () => {
  const maximumEntry = "x".repeat(AFFECTED_PATH_MAX_BYTES);
  assert.equal(validAffectedPaths([maximumEntry]), true);
  assert.match(
    affectedPathsIssue([maximumEntry + "x"])?.message ?? "",
    /at most 512 bytes/
  );

  const maximumCount = Array.from(
    { length: AFFECTED_PATHS_MAX_ITEMS },
    (_, index) => `path-${index}`
  );
  assert.equal(validAffectedPaths(maximumCount), true);
  assert.match(affectedPathsIssue([...maximumCount, "overflow"])?.message ?? "", /at most 64/);

  const aggregateBoundary = Array.from({ length: 32 }, (_, index) => fixedEntry(index, 512));
  assert.equal(
    aggregateBoundary.reduce((total, entry) => total + entry.length, 0),
    AFFECTED_PATHS_MAX_BYTES
  );
  assert.equal(validAffectedPaths(aggregateBoundary), true);
  const aggregateOverflow = [...aggregateBoundary, "x"];
  assert.match(affectedPathsIssue(aggregateOverflow)?.message ?? "", /at most 16384 bytes/);

  assert.equal(affectedPathsIssue(["same", "same"])?.index, 1);
  assert.equal(validAffectedPaths([]), true);
  assert.equal(validAffectedPaths("src/**"), false);
  assert.equal(validAffectedPaths([17]), false);
});

test("draft parsing never trims, sorts, normalizes, or deduplicates", () => {
  const paths = parseAffectedPathsDraft("z-last\nA-first\ncase/Path\ncase/path");
  assert.deepEqual(paths, ["z-last", "A-first", "case/Path", "case/path"]);
  assert.throws(() => parseAffectedPathsDraft(" leading"), AffectedPathsValidationError);
  assert.throws(() => parseAffectedPathsDraft("trailing "), AffectedPathsValidationError);
});

test("component grammar classifies every ASCII byte without locale behavior", () => {
  const safe = new Set(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._@+=,~-*"
  );
  for (let byte = 0; byte <= 0x7f; byte += 1) {
    const character = String.fromCharCode(byte);
    assert.equal(
      validAffectedPaths([`a${character}b`]),
      safe.has(character) || character === "/",
      `ASCII byte 0x${byte.toString(16).padStart(2, "0")}`
    );
  }
  assert.equal(validAffectedPaths(["a/b"]), true, "slash is the only separator");
  for (let byte = 0x80; byte <= 0xff; byte += 1) {
    assert.equal(validAffectedPaths([`a${String.fromCharCode(byte)}b`]), false);
  }
  assert.equal(validAffectedPaths(["a*b*c"]), true);
  assert.equal(validAffectedPaths(["a/**/b"]), true);
  assert.equal(validAffectedPaths(["a**b"]), false);
});
