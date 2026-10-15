import { browserMode, initializeBrowser } from "./browser";
import "./server.css";
async function start() {
  if (browserMode) { document.body.classList.add("browser-mode"); await initializeBrowser(); }
  await import("./main");
}
void start().catch(error => { document.getElementById("root")!.textContent = String(error); });
