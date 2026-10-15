const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const crypto = require("node:crypto");
const {
  Updates,
  select,
  newer,
  checksum,
  WEEK,
} = require("../desktop/updates.cjs");
const bytes = Buffer.from("synthetic installer"),
  sha = crypto.createHash("sha256").update(bytes).digest("hex");
const release = (v = "1.2.4", platform = "linux") => ({
  tag_name: "v" + v,
  assets: [
    platform === "linux"
      ? `alshitasks_${v}_amd64.deb`
      : `alshitasks_${v}_x64_setup.exe`,
    "SHA256SUMS",
  ].map((name) => ({
    name,
    browser_download_url: `https://github.com/Alshival-Ai/alshitasks/releases/download/v${v}/${name}`,
  })),
});
function fixture(t, overrides = {}) {
  const directory = fs.mkdtempSync(
    path.join(os.tmpdir(), "alshitasks-updates-"),
  );
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  let clock = 1000000000000,
    calls = 0;
  const options = {
    directory,
    version: "1.2.3",
    platform: "linux",
    arch: "x64",
    supported: true,
    now: () => clock,
    release: async () => {
      calls++;
      return release();
    },
    text: async () => `${sha}  alshitasks_1.2.4_amd64.deb\n`,
    download: async (_url, file, progress) => {
      fs.writeFileSync(file, bytes);
      progress(100);
    },
    install: async () => true,
    ...overrides,
  };
  const updater = new Updates(options);
  t.after(() => updater.stop());
  return {
    updater,
    options,
    advance: (amount) => (clock += amount),
    calls: () => calls,
  };
}
test("stable semantic versions, platforms, exact release origins and unambiguous checksums", () => {
  assert(newer("1.10.0", "1.9.99"));
  assert(!newer("1.2.3", "1.2.3"));
  assert(!newer("1.2.2", "1.2.3"));
  assert.throws(() => newer("1.2.4-beta", "1.2.3"));
  assert.throws(() =>
    select({ ...release(), prerelease: true }, "linux", "x64"),
  );
  assert.throws(() => select(release(), "linux", "arm64"));
  const wrong = release();
  wrong.assets[0].browser_download_url += "?other";
  assert.throws(() => select(wrong, "linux", "x64"));
  assert.throws(() => select({ ...release(), assets: [] }, "linux", "x64"));
  assert.match(
    select(release("1.2.4", "win32"), "win32", "x64").name,
    /_setup.exe$/,
  );
  assert.throws(() => checksum(`${sha} x\n${sha} x`, "x"));
});
test("weekly checks persist across restart, manual bypass and disabled automatic checks", async (t) => {
  const f = fixture(t);
  await f.updater.tick();
  assert.equal(f.calls(), 1);
  assert.equal(f.updater.state.phase, "ready");
  await f.updater.tick();
  assert.equal(f.calls(), 1);
  const reopened = new Updates(f.options);
  t.after(() => reopened.stop());
  await reopened.start();
  assert.equal(reopened.state.phase, "ready");
  await reopened.tick();
  assert.equal(f.calls(), 1);
  f.advance(WEEK);
  await reopened.tick();
  assert.equal(f.calls(), 2);
  reopened.setEnabled(false);
  f.advance(WEEK);
  await reopened.tick();
  assert.equal(f.calls(), 2);
  await reopened.check();
  assert.equal(f.calls(), 3);
  assert.throws(() => reopened.setEnabled("yes"));
});
test("offline retries respect backoff and server retry instructions", async (t) => {
  const f = fixture(t, {
    release: async () => {
      throw new Error("Offline");
    },
  });
  await f.updater.check();
  assert.equal(f.updater.state.nextCheck - f.options.now(), 3600000);
  await f.updater.check();
  assert.equal(f.updater.state.nextCheck - f.options.now(), 21600000);
  await f.updater.check();
  assert.equal(f.updater.state.nextCheck - f.options.now(), 86400000);
  f.updater.options.release = async () => {
    const e = new Error("Rate limited");
    e.retryAt = f.options.now() + 2 * WEEK;
    throw e;
  };
  await f.updater.check();
  assert.equal(f.updater.state.nextCheck - f.options.now(), 2 * WEEK);
});
test("corrupt downloads never become pending and partial files are removed", async (t) => {
  const f = fixture(t, {
    download: async (_url, file) => fs.writeFileSync(file, "bad"),
  });
  await f.updater.check();
  assert.equal(f.updater.state.phase, "error");
  assert.equal(f.updater.pending, null);
  assert(
    !fs.readdirSync(f.options.directory).some((name) => name.endsWith(".part")),
  );
  assert.equal(await f.updater.install(true), false);
});
test("cached bytes are rechecked before installation and at restart", async (t) => {
  const f = fixture(t);
  await f.updater.check();
  fs.writeFileSync(
    path.join(f.options.directory, f.updater.pending.name),
    "tampered",
  );
  assert.equal(await f.updater.install(true), false);
  const reopened = new Updates(f.options);
  t.after(() => reopened.stop());
  await reopened.start();
  assert.equal(reopened.pending, null);
});
test("single concurrent check and install, authorization cancellation keeps download", async (t) => {
  let releaseCheck;
  const f = fixture(t, {
    release: () =>
      new Promise((resolve) => {
        releaseCheck = resolve;
      }),
  });
  const first = f.updater.check(),
    second = f.updater.check();
  assert.equal(first, second);
  releaseCheck(release());
  await first;
  f.updater.options.install = async () => {
    throw new Error("Authorization canceled");
  };
  assert.equal(await f.updater.install(true), false);
  assert.equal(f.updater.state.phase, "ready");
  assert.match(f.updater.state.error, /canceled/);
  let finish;
  f.updater.options.install = () =>
    new Promise((resolve) => {
      finish = resolve;
    });
  const installing = f.updater.install(false);
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(await f.updater.install(true), false);
  finish(true);
  assert.equal(await installing, true);
});
test("current or older releases never install; source checkouts never check network", async (t) => {
  const f = fixture(t, { release: async () => release("1.2.2") });
  await f.updater.check();
  assert.equal(f.updater.state.phase, "current");
  const dev = fixture(t, { supported: false });
  await dev.updater.tick();
  await dev.updater.check();
  assert.equal(dev.calls(), 0);
});
test("managed desktop reads local status without a second network schedule", async (t) => {
  let calls = 0;
  const f = fixture(t, {
    managed: {
      status: async () => ({
        phase: "ready",
        availableVersion: "1.2.4",
        enabled: true,
      }),
      check: async () => {
        calls++;
      },
    },
  });
  await f.updater.tick();
  assert.equal(f.calls(), 0);
  assert.equal(calls, 0);
  await f.updater.check();
  assert.equal(calls, 1);
  assert.equal(f.calls(), 0);
  assert.throws(() => f.updater.setEnabled(false));
});

test("failed newer downloads retain the verified version and back off repeated download failures", async (t) => {
  const f = fixture(t);
  await f.updater.check();
  f.options.release = async () => release("1.2.5");
  f.options.text = async () => `${sha}  alshitasks_1.2.5_amd64.deb`;
  f.options.download = async () => { throw new Error("Connection lost"); };
  await f.updater.check();
  assert.equal(f.updater.state.phase, "ready");
  assert.equal(f.updater.state.availableVersion, "1.2.4");
  assert.equal(f.updater.pending.version, "1.2.4");
  assert.equal(f.updater.saved.failures, 1);
  await f.updater.check();
  assert.equal(f.updater.saved.failures, 2);
  assert.equal(f.updater.saved.nextCheck - f.options.now(), 21600000);
});
