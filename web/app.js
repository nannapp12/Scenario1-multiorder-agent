// Set window.API_BASE before this script if the API is hosted on another origin.
const API_BASE = window.API_BASE || "";
const $ = (id) => document.getElementById(id);
let chart = null;
let speechToken = null; // { token, region, expires }

function setStatus(msg, isError = false) {
  $("status").textContent = msg;
  $("status").classList.toggle("error", isError);
}

async function getSpeechToken() {
  if (speechToken && Date.now() < speechToken.expires) return speechToken;
  const r = await fetch(`${API_BASE}/api/speech-token`);
  if (!r.ok) throw new Error("Could not get a speech token.");
  const { token, region } = await r.json();
  speechToken = { token, region, expires: Date.now() + 9 * 60 * 1000 }; // tokens last 10 min
  return speechToken;
}

// ---- Speech-to-text (Azure AI Speech) ----
async function listen() {
  const mic = $("mic");
  try {
    const { token, region } = await getSpeechToken();
    const cfg = SpeechSDK.SpeechConfig.fromAuthorizationToken(token, region);
    cfg.speechRecognitionLanguage = "en-US";
    const audio = SpeechSDK.AudioConfig.fromDefaultMicrophoneInput();
    const recognizer = new SpeechSDK.SpeechRecognizer(cfg, audio);

    mic.classList.add("listening");
    setStatus("Listening… speak your question.");
    recognizer.recognizing = (_, e) => { $("question").value = e.result.text; };
    recognizer.recognizeOnceAsync(
      (result) => {
        mic.classList.remove("listening");
        recognizer.close();
        if (result.reason === SpeechSDK.ResultReason.RecognizedSpeech && result.text) {
          $("question").value = result.text;
          ask(result.text);
        } else {
          setStatus("Didn't catch that. Try again or type your question.", true);
        }
      },
      (err) => {
        mic.classList.remove("listening");
        recognizer.close();
        setStatus(`Speech error: ${err}`, true);
      }
    );
  } catch (e) {
    mic.classList.remove("listening");
    setStatus(e.message, true);
  }
}

// ---- Ask the agent ----
async function ask(question) {
  question = question.trim();
  if (question.length < 3) return;
  $("ask").disabled = $("mic").disabled = true;
  setStatus("Querying Databricks, MySQL and Snowflake…");
  try {
    const r = await fetch(`${API_BASE}/api/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || "Request failed.");
    render(data);
    setStatus("");
  } catch (e) {
    setStatus(e.message, true);
  } finally {
    $("ask").disabled = $("mic").disabled = false;
  }
}

// ---- Rendering ----
const isNum = (v) => typeof v === "number" || (typeof v === "string" && v.trim() !== "" && !isNaN(v));
const fmt = (v) => (isNum(v) ? Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 }) : v ?? "");

function render(data) {
  $("result").hidden = false;
  $("answer").textContent = data.answer;

  const table = $("table");
  table.replaceChildren();
  if (data.table && data.table.columns.length) {
    const head = table.createTHead().insertRow();
    data.table.columns.forEach((c) => { const th = document.createElement("th"); th.textContent = c; head.appendChild(th); });
    const body = table.createTBody();
    data.table.rows.forEach((row) => {
      const tr = body.insertRow();
      row.forEach((v) => { const td = tr.insertCell(); td.textContent = fmt(v); if (isNum(v)) td.className = "num"; });
    });
  }
  table.parentElement.hidden = !data.table;

  renderChart(data);

  const sql = $("sql");
  sql.replaceChildren();
  (data.queries || []).forEach((q) => {
    const h = document.createElement("strong"); h.textContent = q.source;
    const pre = document.createElement("pre"); pre.textContent = q.sql;
    sql.append(h, pre);
  });
  $("sql-details").hidden = !(data.queries || []).length;
}

function renderChart(data) {
  if (chart) { chart.destroy(); chart = null; }
  const spec = data.chart, t = data.table;
  $("chart-wrap").hidden = !(spec && t);
  if (!spec || !t) return;

  const xi = t.columns.indexOf(spec.x);
  const css = getComputedStyle(document.documentElement);
  const palette = ["--accent", "--listening", "--muted"].map((v) => css.getPropertyValue(v).trim());
  chart = new Chart($("chart"), {
    type: "bar",
    data: {
      labels: t.rows.map((r) => r[xi]),
      datasets: spec.y.map((col, i) => ({
        label: col,
        data: t.rows.map((r) => Number(r[t.columns.indexOf(col)])),
        backgroundColor: palette[i % palette.length],
        borderRadius: 4,
      })),
    },
    options: {
      responsive: true,
      plugins: {
        title: { display: !!spec.title, text: spec.title, color: css.getPropertyValue("--text") },
        legend: { display: spec.y.length > 1 },
      },
      scales: {
        x: { ticks: { color: css.getPropertyValue("--muted") }, grid: { display: false } },
        y: { beginAtZero: true, ticks: { color: css.getPropertyValue("--muted") },
             grid: { color: css.getPropertyValue("--border") } },
      },
    },
  });
}

$("mic").addEventListener("click", listen);
$("ask-form").addEventListener("submit", (e) => { e.preventDefault(); ask($("question").value); });
