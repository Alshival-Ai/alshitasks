const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");

const marker = "X-Alshival-Managed=true";
function startupFile() {
  const config = process.env.ALSHITASKS_TEST_ROOT
    ? path.join(process.env.ALSHITASKS_TEST_ROOT, "config")
    : process.env.XDG_CONFIG_HOME || path.join(os.homedir(), ".config");
  return path.join(config, "autostart", "alshitasks.desktop");
}
function readEntry() {
  try {
    return fs.readFileSync(startupFile(), "utf8");
  } catch (error) {
    if (error.code === "ENOENT") return "";
    throw error;
  }
}
function getStartup() {
  const text = readEntry();
  return {
    enabled:
      Boolean(text) &&
      !/^Hidden\s*=\s*true\s*$/im.test(text) &&
      !/^X-GNOME-Autostart-enabled\s*=\s*false\s*$/im.test(text) &&
      /^Exec\s*=\s*\/usr\/bin\/alshitasks desktop --start-in-tray\s*$/m.test(
        text,
      ),
  };
}
function setStartup(enabled) {
  if (typeof enabled !== "boolean")
    throw new Error("Invalid startup preference");
  const text = readEntry();
  if (text && !text.split(/\r?\n/).includes(marker))
    throw new Error(
      "An existing custom Alshival.Ai startup entry must be managed in Ubuntu Startup Applications.",
    );
  const file = startupFile();
  if (!enabled) {
    if (text) fs.unlinkSync(file);
    return getStartup();
  }
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const temporary = file + "." + process.pid + ".tmp";
  try {
    fs.writeFileSync(
      temporary,
      `[Desktop Entry]\nType=Application\nName=Alshival.Ai\nComment=Start Alshival.Ai in the top bar\nExec=/usr/bin/alshitasks desktop --start-in-tray\nTryExec=/usr/bin/alshitasks\nIcon=alshitasks\nTerminal=false\nHidden=false\nX-GNOME-Autostart-enabled=true\n${marker}\n`,
      { mode: 0o600, flag: "wx" },
    );
    fs.renameSync(temporary, file);
  } finally {
    try {
      fs.unlinkSync(temporary);
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
  }
  return getStartup();
}
module.exports = { getStartup, setStartup };
