const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const root = fs.mkdtempSync(path.join(os.tmpdir(), "alshitasks-login-test-"));
process.env.ALSHITASKS_TEST_ROOT = root;
const { getStartup, setStartup } = require("../desktop/startup.cjs");
const file = path.join(root, "config/autostart/alshitasks.desktop");
try {
  assert.deepEqual(getStartup(), { enabled: false });
  assert.throws(() => setStartup("yes"), /Invalid/);
  assert.deepEqual(setStartup(true), { enabled: true });
  const entry = fs.readFileSync(file, "utf8");
  assert.match(entry, /TryExec=\/usr\/bin\/alshitasks/);
  fs.writeFileSync(file, entry.replace("Hidden=false", "Hidden=true"));
  assert.equal(getStartup().enabled, false);
  setStartup(true);
  fs.writeFileSync(
    file,
    entry.replace(
      "X-GNOME-Autostart-enabled=true",
      "X-GNOME-Autostart-enabled=false",
    ),
  );
  assert.equal(getStartup().enabled, false);
  setStartup(false);
  assert.equal(fs.existsSync(file), false);
  fs.writeFileSync(file, "[Desktop Entry]\nExec=custom\n");
  assert.throws(() => setStartup(true), /custom/);
  assert.throws(() => setStartup(false), /custom/);
  assert.match(fs.readFileSync(file, "utf8"), /Exec=custom/);
  fs.unlinkSync(file);
  fs.rmdirSync(path.dirname(file));
  fs.writeFileSync(path.dirname(file), "not a directory");
  assert.throws(() => setStartup(true));
  console.log(
    "Startup preferences: default, toggle, external disable, custom entry and filesystem failure passed.",
  );
} finally {
  fs.rmSync(root, { recursive: true, force: true });
}
