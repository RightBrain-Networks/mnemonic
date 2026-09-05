import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const read = (path) => readFile(new URL(`../${path}`, import.meta.url), "utf8");

test("dashboard package and lock ship the coordinated current release version", async () => {
  const manifest = JSON.parse(await read("package.json"));
  const lock = JSON.parse(await read("package-lock.json"));
  assert.equal(manifest.version, "0.7.0");
  assert.equal(lock.version, "0.7.0");
  assert.equal(lock.packages[""].version, "0.7.0");
});

test("full checkpoints carry scope while compact pointers stay unchanged", async () => {
  const types = await read("lib/types.ts");
  const pointer = types.match(
    /export interface CheckpointPointer \{(?<body>[\s\S]*?)\n\}/
  )?.groups?.body;
  const full = types.match(
    /export interface Checkpoint extends CheckpointPointer \{(?<body>[\s\S]*?)\n\}/
  )?.groups?.body;
  assert.ok(pointer);
  assert.ok(full);
  assert.doesNotMatch(pointer, /affected_paths/);
  assert.match(full, /affected_paths: string\[\]/);
});

test("checkpoint repository evidence is inert, isolated, and explicitly declaration-only", async () => {
  const display = await read("components/checkpoint-repository-declaration.tsx");
  const editor = await read("components/affected-paths-editor.tsx");
  const pane = await read("components/work-detail-pane.tsx");
  assert.match(display, /Caller-declared branch/);
  assert.match(display, /Caller-asserted baseline/);
  assert.match(display, /Declared affected paths/);
  assert.match(display, /No dependency scope declared/);
  assert.match(display, /Not assessed by this browser\./);
  assert.match(display, /<bdi dir="auto">/);
  assert.match(display, /<bdi dir="ltr">/);
  assert.doesNotMatch(display, /<button|<a\s|verified-badge|fresh-badge/i);

  assert.match(editor, /One pattern per line/);
  assert.match(editor, /<div className="field affected-paths-editor">/);
  assert.match(editor, /<label htmlFor=\{inputId\}>Declared affected paths<\/label>/);
  assert.doesNotMatch(editor, /<label className="field affected-paths-editor"/);
  assert.match(editor, /aria-describedby/);
  assert.match(editor, /aria-invalid/);
  assert.match(editor, /role="alert"/);
  assert.match(editor, /does not assess a local repository/);

  assert.match(pane, /<CheckpointRepositoryDeclaration checkpoint=\{current\}/);
  assert.match(pane, /<AffectedPathsEditor/);
});

test("browser code contains no local repository verifier or filesystem process path", async () => {
  const sources = await Promise.all([
    read("components/dashboard.tsx"),
    read("components/work-detail-pane.tsx"),
    read("lib/affected-paths.ts"),
    read("lib/proxy-policy.ts"),
    read("app/api/mnemonic/[...path]/route.ts")
  ]);
  const source = sources.join("\n");
  assert.doesNotMatch(
    source,
    /node:child_process|child_process|execFile|spawnSync|showOpenFilePicker|FileSystemDirectoryHandle/
  );
  assert.doesNotMatch(source, /mnemonic-repository-freshness|GIT_DIR|git\s+diff/);
});
