"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const tabs = [...document.querySelectorAll("[data-stage]")];
  const stageCopy = [
    ["The updater accepts a valid ID.", "Compare the returned customer with the job’s selected source."],
    ["Bind the job. Check the actual arguments.", "Forced boundary probes and model tool calls are recorded separately below."],
    ["The source changes. The binding stays.", "Compare the stale-ID probe with the model’s refreshed current-source update."],
  ];
  const controlKeys = [
    [],
    [["blocked_wrong_customer", "Wrong customer"], ["blocked_wrong_job", "Wrong job"]],
    [["blocked_stale_source", "Stale customer after rollover"], ["blocked_broken_source", "Broken source JSON"]],
  ];
  let recording = null;
  let activeStage = 0;

  const isObject = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
  const parse = (value) => {
    if (typeof value !== "string") return value;
    try { return JSON.parse(value); } catch { return value; }
  };
  const pick = (value, keys) => {
    if (!isObject(value)) return value;
    return Object.fromEntries(keys.filter((key) => Object.hasOwn(value, key)).map((key) => [key, value[key]]));
  };
  const jsonText = (value) => typeof value === "string" ? value : JSON.stringify(value, null, 2);
  const node = (tag, className, text) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  };

  // Render only business evidence; provider, usage, cost and runtime metadata stay out.
  function evidenceResult(raw) {
    const value = parse(raw);
    if (!isObject(value)) return value;
    const clean = pick(value, ["ok", "demo", "updated_customer", "status", "binding_status", "error", "message", "action"]);
    // Business identity comes first; native scope/path metadata stays inspectable below.
    if (isObject(value.current)) clean.current = pick(value.current, ["job", "customer_id"]);
    else if (Object.hasOwn(value, "current")) clean.current = value.current;
    if (isObject(value.record)) clean.record = pick(value.record, ["customer_id", "name", "job"]);
    if (isObject(value.binding)) clean.binding = pick(value.binding, ["scope_id", "job", "source", "protected_tool"]);
    else if (Object.hasOwn(value, "binding")) clean.binding = value.binding;
    Object.assign(clean, pick(value, ["scope_id", "next_step"]));
    return clean;
  }

  function outcome(raw) {
    const value = parse(raw);
    const text = typeof value === "string" ? value : jsonText(pick(value, ["error", "message", "action", "status"]));
    if (isObject(value) && value.ok === true) return ["Accepted", "accepted"];
    if (isObject(value) && (value.action === "block" || value.status === "blocked")) return ["Blocked", "blocked"];
    if (/\bblocked?\b/i.test(text || "")) return ["Blocked", "blocked"];
    if (isObject(value) && Object.hasOwn(value, "error")) return ["Error", "error"];
    if (isObject(value) && value.status === "ready") return ["Current source", "current"];
    return ["Recorded", "preparing"];
  }

  function badge(raw) {
    const [label, style] = outcome(raw);
    return node("span", `outcome-badge ${style}`, label);
  }

  function viewFromStage(stage) {
    if (!stage || !Array.isArray(stage.calls)) return null;
    for (const call of [...stage.calls].reverse()) {
      const value = parse(call.result);
      if (call.tool === "sourcebind_current" && isObject(value) && (Object.hasOwn(value, "status") || Object.hasOwn(value, "current") || Object.hasOwn(value, "binding"))) return value;
    }
    return isObject(stage.view) ? stage.view : null;
  }

  function renderSource(stage) {
    const view = viewFromStage(stage);
    const selected = stage && isObject(stage.selected_source) ? pick(stage.selected_source, ["job", "customer_id"]) : null;
    const current = selected || view && view.current;
    $("source-job").textContent = current && current.job || "—";
    $("source-id").textContent = current && current.customer_id || "—";
    $("source-provenance").textContent = selected ? "Recorded pre-turn source" : current ? "Recorded current view" : "Awaiting recording";
    const update = stage && Array.isArray(stage.calls) ? [...stage.calls].reverse().find((call) => call.tool === "sourcebind_demo_update" && Object.hasOwn(call, "result")) : null;
    const updateResult = update && parse(update.result);
    const bindingState = view && Object.hasOwn(view, "binding") ? view.binding ? "bound" : view.status === "unbound" ? "unbound" : null : isObject(updateResult) ? updateResult.binding_status : null;
    $("binding-state").textContent = bindingState === "bound" ? "Job bound to current source" : bindingState === "unbound" ? "Unbound baseline" : "Recorded result preparing";
    const protectedTool = view && view.binding && view.binding.protected_tool;
    $("binding-note").textContent = bindingState === "bound" ? `${protectedTool ? `Protected tool: ${protectedTool}. ` : ""}The file is read again at each protected call.` : bindingState === "unbound" ? "The updater receives a valid ID without this job/source binding." : "The selected JSON supplies the customer identity for this job.";
    const bindingEvidence = view ? evidenceResult(view) : isObject(updateResult) ? pick(updateResult, ["scope_id", "binding_status"]) : null;
    $("binding-json").textContent = bindingEvidence ? jsonText(bindingEvidence) : "Awaiting this stage’s recorded binding.";
    $("binding-command").textContent = recording && recording.binding ? typeof recording.binding === "string" ? recording.binding : jsonText(evidenceResult(recording.binding)) : "Awaiting the recorded user bind command output.";
  }

  function renderOutcome(stage) {
    const calls = stage && Array.isArray(stage.calls) ? stage.calls : [];
    const update = [...calls].reverse().find((call) => call.tool === "sourcebind_demo_update" && Object.hasOwn(call, "result"));
    if (!update) {
      $("outcome-badge").className = "outcome-badge preparing";
      $("outcome-badge").textContent = "Preparing";
      $("outcome-text").textContent = stage ? "The stage’s updater result is preparing." : "Recorded results appear here when this stage is ready.";
      return;
    }
    const value = parse(update.result);
    const [label, style] = outcome(value);
    $("outcome-badge").className = `outcome-badge ${style}`;
    $("outcome-badge").textContent = label;
    $("outcome-text").textContent = isObject(value) && value.ok === true && value.updated_customer ? `The updater returned updated_customer: ${value.updated_customer}.` : "The updater’s actual result is recorded below.";
  }

  function renderControls(stage) {
    const target = $("controls");
    target.replaceChildren();
    if (!stage) {
      target.append(node("p", "empty-note", "Awaiting this stage’s recorded boundary checks."));
      return;
    }
    if (activeStage === 0) {
      target.append(node("p", "empty-note", "The baseline is unbound. Select stage 2 to inspect the forced identity checks."));
      return;
    }
    const checks = [...controlKeys[activeStage]];
    if (activeStage === 2 && Object.hasOwn(recording, "restored_current")) checks.push(["restored_current", "Repaired source read"]);
    for (const [key, label] of checks) {
      const details = node("details", "control");
      details.dataset.control = key;
      const summary = node("summary");
      summary.append(node("span", "control-label", label));
      const hasResult = recording && Object.hasOwn(recording, key);
      summary.append(hasResult ? badge(recording[key]) : node("span", "outcome-badge preparing", "Preparing"));
      details.append(summary, node("pre", "", hasResult ? jsonText(evidenceResult(recording[key])) : "Awaiting this native control result."));
      target.append(details);
    }
  }

  function renderCalls(stage) {
    const target = $("model-calls");
    target.replaceChildren();
    const calls = stage && Array.isArray(stage.calls) ? stage.calls : [];
    if (!calls.length) target.append(node("p", "empty-note", stage ? "This stage’s tool-call record is empty." : "Place the recorded native-demo.json beside this page, then reload."));
    for (const [index, call] of calls.entries()) {
      const details = node("details", "call-row");
      details.dataset.call = String(index);
      details.dataset.tool = typeof call.tool === "string" ? call.tool : "";
      const summary = node("summary");
      const args = pick(parse(call.args), ["job", "customer_id"]);
      summary.append(node("span", "call-order", String(index + 1)), node("span", "call-name", typeof call.tool === "string" ? call.tool : "Recorded tool"), node("span", "call-customer", args && args.customer_id || ""), Object.hasOwn(call, "result") ? badge(call.result) : node("span", "outcome-badge preparing", "Preparing"));
      const fields = node("div", "call-fields");
      const argsColumn = node("div");
      argsColumn.append(node("h4", "", "Arguments"), node("pre", "", jsonText(args === undefined ? {} : args)));
      const resultColumn = node("div");
      resultColumn.append(node("h4", "", "Result fields"), node("pre", "", Object.hasOwn(call, "result") ? jsonText(evidenceResult(call.result)) : "Result preparing."));
      fields.append(argsColumn, resultColumn);
      details.append(summary, fields);
      // One result stays readable at a glance; every recorded call remains inspectable.
      details.open = index === calls.length - 1;
      target.append(details);
    }
    $("recorded-label").textContent = stage && typeof stage.label === "string" ? stage.label : "Stage record preparing";
    $("final-text").textContent = stage && typeof stage.final === "string" && stage.final.trim() ? stage.final : "Awaiting this stage’s recorded final response.";
  }

  function render() {
    const stage = recording && recording.stages[activeStage];
    $("stage-title").textContent = stageCopy[activeStage][0];
    $("stage-description").textContent = stageCopy[activeStage][1];
    const panel = $("stage-panel");
    panel.dataset.activeStage = String(activeStage);
    panel.setAttribute("aria-labelledby", `tab-${activeStage}`);
    for (const [index, tab] of tabs.entries()) {
      tab.setAttribute("aria-selected", String(index === activeStage));
      tab.tabIndex = index === activeStage ? 0 : -1;
    }
    renderSource(stage);
    renderOutcome(stage);
    renderControls(stage);
    renderCalls(stage);
  }

  function selectStage(index, focus = false) {
    activeStage = index;
    render();
    if (focus) tabs[index].focus({ preventScroll: true });
    if (!window.matchMedia("(prefers-reduced-motion: reduce)").matches && $("source-id").textContent !== "—" && $("source-id").animate) {
      $("source-id").animate([{ color: "#ff5425" }, { color: "#18201b" }], { duration: 400, easing: "ease-out" });
    }
  }

  async function loadRecording() {
    const reload = document.querySelector('[data-action="reload"]');
    reload.disabled = true;
    $("load-status-text").textContent = "Preparing recorded data";
    document.body.classList.remove("data-ready");
    try {
      const response = await fetch("./native-demo.json", { cache: "no-store" });
      if (!response.ok) throw new Error("Recording file is preparing.");
      const data = await response.json();
      const root = isObject(data.result) ? data.result : data;
      if (!isObject(root) || !Array.isArray(root.stages)) throw new Error("Recording needs a stages array.");
      recording = root;
      $("load-status-text").textContent = "Loaded recorded native-demo.json";
      document.body.classList.add("data-ready");
    } catch {
      recording = null;
      $("load-status-text").textContent = "Run result preparing: native-demo.json";
    } finally {
      reload.disabled = false;
      render();
    }
  }

  for (const tab of tabs) tab.addEventListener("click", () => selectStage(Number(tab.dataset.stage)));
  document.querySelector('[data-action="reload"]').addEventListener("click", loadRecording);
  document.addEventListener("keydown", (event) => {
    if (event.ctrlKey || event.altKey || event.metaKey || /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName) || event.target.isContentEditable) return;
    if (["1", "2", "3"].includes(event.key)) {
      event.preventDefault();
      selectStage(Number(event.key) - 1, true);
    } else if (event.target.matches('[role="tab"]') && ["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      const next = event.key === "Home" ? 0 : event.key === "End" ? 2 : (activeStage + (event.key === "ArrowRight" ? 1 : 2)) % 3;
      selectStage(next, true);
    }
  });
  loadRecording();
})();
