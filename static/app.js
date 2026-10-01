/* SPDX-License-Identifier: AGPL-3.0-only */
/* Copyright (C) 2026 Ahmet Harun Ertürk */
(function () {
  "use strict";

  /* Branding / copy comes from the server-rendered window.__APP_META__
     (see branding.py). Fallbacks below keep the historical defaults. */
  var META = window.__APP_META__ || {};
  var UNIT_SINGULAR = META.unitSingular || "object";
  var UNIT_PLURAL = META.unitPlural || "objects";
  var UPLOAD_LIMIT_MB = META.uploadLimitMb || 10;

  var MAX_SIZE_BYTES = UPLOAD_LIMIT_MB * 1024 * 1024;
  var ACCEPT_PATTERN = /\.(jpe?g|png|webp)$/i;
  var MODEL_STORE_KEY = "cvd.model";

  var TEXT = {
    detecting: "Detecting\u2026",
    detectBtn: META.detectBtn || ("Detect " + UNIT_PLURAL),
    emptyTitle: META.emptyTitle || ("No " + UNIT_PLURAL + " detected"),
    zeroCount: "0 " + UNIT_PLURAL,
    topConf: "Highest confidence: ",
    errorGeneric: "Prediction failed. Please try again."
  };

  function unitFor(count) { return count === 1 ? UNIT_SINGULAR : UNIT_PLURAL; }

  function labelOf(det) { return det && det.display_class ? det.display_class : (det ? det.class : "\u2014"); }

  function sizeLimitMessage() { return "Image is larger than the " + UPLOAD_LIMIT_MB + " MB limit."; }

  var els = {
    statusBadge: document.getElementById("statusBadge"),
    statusText: document.getElementById("statusText"),
    errorAlert: document.getElementById("errorAlert"),
    errorText: document.getElementById("errorText"),
    dropzone: document.getElementById("dropzone"),
    dropTitle: document.getElementById("dropTitle"),
    fileInput: document.getElementById("fileInput"),
    chooseBtn: document.getElementById("chooseBtn"),
    preview: document.getElementById("preview"),
    previewImg: document.getElementById("previewImg"),
    metaName: document.getElementById("metaName"),
    metaDetail: document.getElementById("metaDetail"),
    result: document.getElementById("result"),
    resultImg: document.getElementById("resultImg"),
    countBadge: document.getElementById("countBadge"),
    summary: document.getElementById("summary"),
    tableWrap: document.getElementById("tableWrap"),
    tableBody: document.getElementById("tableBody"),
    emptyState: document.getElementById("emptyState"),
    modelPicker: document.getElementById("modelPicker"),
    confInput: document.getElementById("confInput")
  };

  var selectedFile = null;
  var busy = false;
  var previewUrl = null;
  var dragDepth = 0;

  /* ------------------------------------------------ model status */

  function setModelState(ready) {
    els.statusBadge.setAttribute("data-state", ready ? "ready" : "error");
    els.statusText.textContent = ready ? "Model Ready" : "Model unavailable";
  }

  function fetchModelStatus() {
    fetch("/health")
      .then(function (res) { return res.json(); })
      .then(function (data) { setModelState(Boolean(data.model_loaded)); })
      .catch(function () { setModelState(false); });
  }

  /* ------------------------------------------------ model picker */

  function currentModelName() {
    return els.modelPicker && els.modelPicker.value ? els.modelPicker.value : "";
  }

  function populateModels(defaultName, models) {
    if (!els.modelPicker) { return; }
    var saved = null;
    try { saved = window.localStorage.getItem(MODEL_STORE_KEY); } catch (err) { saved = null; }
    els.modelPicker.innerHTML = "";
    var names = [];
    models.forEach(function (entry) {
      var option = document.createElement("option");
      option.value = entry.name;
      option.textContent = entry.name;
      els.modelPicker.appendChild(option);
      names.push(entry.name);
    });
    if (saved && names.indexOf(saved) !== -1) {
      els.modelPicker.value = saved;
    } else if (names.indexOf(defaultName) !== -1) {
      els.modelPicker.value = defaultName;
    }
    els.modelPicker.disabled = names.length === 0;
  }

  function fetchModelCatalog() {
    fetch("/models")
      .then(function (res) {
        if (res.status === 401) {
          window.location.href = "/login";
          throw new Error("Please sign in to continue.");
        }
        return res.json();
      })
      .then(function (data) {
        populateModels(data.default, Array.isArray(data.models) ? data.models : []);
      })
      .catch(function () {
        if (els.modelPicker) { els.modelPicker.disabled = true; }
      });
  }

  /* ------------------------------------------------ error / state helpers */

  function showError(message) {
    els.errorText.textContent = message;
    els.errorAlert.hidden = false;
  }

  function clearError() {
    els.errorAlert.hidden = true;
    els.errorText.textContent = "";
  }

  function clearResult() {
    els.result.hidden = true;
    els.resultImg.removeAttribute("src");
    els.tableBody.innerHTML = "";
    els.tableWrap.hidden = true;
    els.summary.hidden = true;
    els.emptyState.hidden = true;
  }

  function setBusy(state) {
    busy = state;
    els.chooseBtn.disabled = state;
    els.dropzone.classList.toggle("is-busy", state);
  }

  function formatBytes(bytes) {
    if (bytes < 1024) { return bytes + " B"; }
    if (bytes < 1024 * 1024) { return (bytes / 1024).toFixed(1) + " KB"; }
    return (bytes / (1024 * 1024)).toFixed(1) + " MB";
  }

  /* ------------------------------------------------ file selection */

  function acceptFile(file) {
    if (!file) { return; }
    if (!ACCEPT_PATTERN.test(file.name)) {
      showError("Unsupported image format. Use JPG, JPEG, PNG or WEBP.");
      return;
    }
    if (file.size > MAX_SIZE_BYTES) {
      showError(sizeLimitMessage());
      return;
    }

    clearError();
    clearResult();
    selectedFile = file;
    renderPreview(file);
    runDetection();
  }

  function renderPreview(file) {
    if (previewUrl) { URL.revokeObjectURL(previewUrl); }
    previewUrl = URL.createObjectURL(file);
    els.previewImg.onload = function () {
      els.metaName.textContent = file.name;
      els.metaDetail.textContent =
        els.previewImg.naturalWidth + " \u00d7 " + els.previewImg.naturalHeight +
        "  \u00b7  " + formatBytes(file.size);
    };
    els.previewImg.src = previewUrl;
    els.dropzone.hidden = true;
    els.preview.hidden = false;
    els.preview.scrollIntoView({ block: "nearest" });
  }

  function openPicker() {
    if (!busy) { els.fileInput.click(); }
  }

  /* ------------------------------------------------ result rendering */

  function renderResult(data) {
    var mime = data.image_format === "png" ? "png" : "jpeg";
    els.resultImg.src = "data:image/" + mime + ";base64," + data.image;

    var detections = Array.isArray(data.detections) ? data.detections : [];
    var count = detections.length;
    els.countBadge.textContent = count + " " + unitFor(count);

    if (count === 0) {
      els.summary.hidden = true;
      els.tableWrap.hidden = true;
      els.tableBody.innerHTML = "";
      els.emptyState.hidden = false;
    } else {
      var top = detections[0].confidence;
      for (var i = 1; i < detections.length; i += 1) {
        if (detections[i].confidence > top) { top = detections[i].confidence; }
      }
      els.summary.textContent =
        count + " " + unitFor(count) + " detected" +
        "  \u00b7  " + TEXT.topConf + (top * 100).toFixed(1) + "%";
      els.summary.hidden = false;
      els.emptyState.hidden = true;
      renderTable(detections);
      els.tableWrap.hidden = false;
    }

    // Once the prediction lands: hide the uploaded photo, keep only the
    // detection result visible and reopen the upload area for the next run.
    els.preview.hidden = true;
    els.dropzone.hidden = false;
    els.dropTitle.textContent = "Drop another image here";
    els.result.hidden = false;
    els.result.scrollIntoView({ block: "start" });
  }

  function renderTable(detections) {
    els.tableBody.innerHTML = "";
    detections.forEach(function (det) {
      var row = document.createElement("tr");

      var classCell = document.createElement("td");
      var badge = document.createElement("span");
      badge.className = "chip chip-class";
      badge.textContent = labelOf(det);
      classCell.appendChild(badge);

      var confCell = document.createElement("td");
      confCell.className = "cell-numbers";
      confCell.textContent = (det.confidence * 100).toFixed(1) + "%";

      var coordCell = document.createElement("td");
      coordCell.className = "cell-coords";
      coordCell.textContent =
        det.bbox.x1 + ", " + det.bbox.y1 + " \u2192 " + det.bbox.x2 + ", " + det.bbox.y2;

      row.appendChild(classCell);
      row.appendChild(confCell);
      row.appendChild(coordCell);
      els.tableBody.appendChild(row);
    });
  }

  /* ------------------------------------------------ prediction */

  function apiErrorMessage(status, payload) {
    if (payload && typeof payload.detail === "string") { return payload.detail; }
    if (payload && Array.isArray(payload.detail) && payload.detail[0] && payload.detail[0].msg) {
      return payload.detail[0].msg;
    }
    if (status === 413) { return sizeLimitMessage(); }
    return TEXT.errorGeneric;
  }

  function runDetection() {
    if (busy || !selectedFile) { return; }

    clearError();
    clearResult();
    setBusy(true);

    var form = new FormData();
    form.append("image", selectedFile);
    var chosenModel = currentModelName();
    if (chosenModel) { form.append("model", chosenModel); }
    var confValue = els.confInput ? parseFloat(els.confInput.value) : NaN;
    if (isFinite(confValue) && confValue > 0 && confValue < 1) {
      form.append("confidence", confValue.toFixed(2));
    }

    fetch("/predict", { method: "POST", body: form })
      .then(function (res) {
        return res.text().then(function (text) {
          if (res.status === 401) {
            window.location.href = "/login";
            throw new Error("Please sign in to continue.");
          }
          var payload = null;
          try { payload = JSON.parse(text); } catch (err) { payload = null; }
          if (!res.ok || !payload || payload.success !== true) {
            throw new Error(apiErrorMessage(res.status, payload));
          }
          return payload;
        });
      })
      .then(renderResult)
      .catch(function (err) {
        showError(err && err.message ? err.message : TEXT.errorGeneric);
        els.preview.hidden = true;
        els.dropzone.hidden = false;
      })
      .then(function () { setBusy(false); });
  }

  /* ------------------------------------------------ wiring */

  els.chooseBtn.addEventListener("click", function (event) {
    event.stopPropagation();
    openPicker();
  });
  els.dropzone.addEventListener("click", openPicker);
  els.dropzone.addEventListener("keydown", function (event) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openPicker();
    }
  });

  els.fileInput.addEventListener("change", function () {
    acceptFile(els.fileInput.files && els.fileInput.files[0]);
    els.fileInput.value = "";
  });

  ["dragenter", "dragover"].forEach(function (name) {
    els.dropzone.addEventListener(name, function (event) {
      event.preventDefault();
      if (name === "dragenter") { dragDepth += 1; }
      els.dropzone.classList.add("drag-over");
    });
  });
  els.dropzone.addEventListener("dragleave", function () {
    dragDepth = Math.max(0, dragDepth - 1);
    if (dragDepth === 0) { els.dropzone.classList.remove("drag-over"); }
  });
  els.dropzone.addEventListener("drop", function (event) {
    event.preventDefault();
    dragDepth = 0;
    els.dropzone.classList.remove("drag-over");
    acceptFile(event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0]);
  });
  ["dragover", "drop"].forEach(function (name) {
    window.addEventListener(name, function (event) { event.preventDefault(); });
  });

  if (els.modelPicker) {
    els.modelPicker.addEventListener("change", function () {
      try {
        window.localStorage.setItem(MODEL_STORE_KEY, els.modelPicker.value);
      } catch (err) { /* storage unavailable - selection simply is not persisted */ }
    });
  }

  fetchModelStatus();
  fetchModelCatalog();
})();
