(function () {
  const drawerRoot = document.querySelector("[data-job-drawer]");
  const drawerPanel = drawerRoot ? drawerRoot.querySelector(".job-drawer-panel") : null;
  const drawerContent = drawerRoot ? drawerRoot.querySelector("[data-job-drawer-content]") : null;
  const drawerTitle = drawerRoot ? drawerRoot.querySelector("#job-drawer-title") : null;
  let activeTrigger = null;
  let activeSegment = null;

  function initUploadForms() {
    document.querySelectorAll("[data-upload-form]").forEach(function (form) {
      const input = form.querySelector("[data-upload-file-input]");
      const dropzone = form.querySelector("[data-upload-dropzone]");
      const fileName = form.querySelector("[data-upload-file-name]");
      const submitButton = form.querySelector("[data-upload-submit]");

      if (!input || !dropzone || !fileName) {
        return;
      }

      function syncFileName() {
        const file = input.files && input.files[0];
        if (!file) {
          fileName.textContent = "选择后会在这里显示文件名";
          dropzone.classList.remove("has-file");
          return;
        }
        fileName.textContent = `已选择：${file.name}`;
        dropzone.classList.add("has-file");
      }

      input.addEventListener("change", syncFileName);
      dropzone.addEventListener("dragover", function (event) {
        event.preventDefault();
        dropzone.classList.add("is-dragging");
      });
      dropzone.addEventListener("dragleave", function () {
        dropzone.classList.remove("is-dragging");
      });
      dropzone.addEventListener("drop", function (event) {
        event.preventDefault();
        dropzone.classList.remove("is-dragging");
        if (!event.dataTransfer || !event.dataTransfer.files.length) {
          return;
        }
        input.files = event.dataTransfer.files;
        syncFileName();
      });
      form.addEventListener("submit", function () {
        if (submitButton) {
          submitButton.disabled = true;
          submitButton.textContent = "提交中...";
        }
      });

      syncFileName();
    });
  }

  function parseSeconds(value) {
    const parsed = Number.parseFloat(value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function formatSeconds(value) {
    if (!Number.isFinite(value)) {
      return "-";
    }
    const formatted = value.toFixed(3).replace(/\.?0+$/, "");
    return `${formatted}s`;
  }

  function clearActiveSegment(options) {
    const settings = Object.assign({ pause: false, resetLabel: true }, options);
    if (!activeSegment) {
      return;
    }

    activeSegment.audio.removeEventListener("timeupdate", handleAudioTimeUpdate);
    activeSegment.audio.removeEventListener("ended", handleAudioEnded);

    if (settings.pause) {
      activeSegment.audio.pause();
    }
    if (settings.resetLabel) {
      activeSegment.button.textContent = "播放";
    }
    activeSegment.row.classList.remove("is-playing");
    activeSegment = null;
  }

  function handleAudioTimeUpdate(event) {
    if (!activeSegment || event.target !== activeSegment.audio) {
      return;
    }
    if (Number.isFinite(activeSegment.endSec) && event.target.currentTime >= activeSegment.endSec) {
      event.target.pause();
      event.target.currentTime = activeSegment.endSec;
      activeSegment.status.textContent = `${activeSegment.segmentId} 已播放至 ${formatSeconds(activeSegment.endSec)}`;
      clearActiveSegment();
    }
  }

  function handleAudioEnded(event) {
    if (!activeSegment || event.target !== activeSegment.audio) {
      return;
    }
    activeSegment.status.textContent = `${activeSegment.segmentId} 已播放结束`;
    clearActiveSegment();
  }

  function showAudioError(audio) {
    const section = audio.closest(".detail-section") || document;
    const status = section.querySelector("[data-current-segment]");
    if (status) {
      status.textContent = "本地音频文件加载失败，请检查 postcall_audio_assets 对应文件是否存在";
    }
  }

  function playSegment(button) {
    const section = button.closest(".detail-section") || document;
    const audio = section.querySelector("[data-job-audio]");
    const startSec = parseSeconds(button.dataset.startSec);
    const endSec = parseSeconds(button.dataset.endSec);
    const segmentId = button.dataset.segmentId || "当前片段";
    const row = button.closest("[data-segment-row]");
    const status = section.querySelector("[data-current-segment]");

    if (!audio || startSec === null || !row || !status) {
      return;
    }

    clearActiveSegment({ pause: true });

    const nextSegment = {
      audio,
      button,
      endSec,
      row,
      segmentId,
      status,
    };
    activeSegment = nextSegment;

    audio.currentTime = startSec;
    audio.addEventListener("timeupdate", handleAudioTimeUpdate);
    audio.addEventListener("ended", handleAudioEnded);
    button.textContent = "播放中";
    row.classList.add("is-playing");
    status.textContent = `${segmentId} ${formatSeconds(startSec)} - ${formatSeconds(endSec)}`;

    const playResult = audio.play();
    if (playResult && typeof playResult.catch === "function") {
      playResult.catch(function () {
        if (activeSegment !== nextSegment) {
          return;
        }
        if (audio.error) {
          status.textContent = `${segmentId} 本地音频文件加载失败`;
        } else {
          status.textContent = `${segmentId} 已定位到 ${formatSeconds(startSec)}，请手动点击音频播放`;
        }
        clearActiveSegment({ resetLabel: true });
      });
    }
  }

  function openDrawerShell() {
    drawerRoot.hidden = false;
    drawerRoot.setAttribute("aria-hidden", "false");
    drawerRoot.classList.add("is-open");
    document.body.classList.add("has-job-drawer-open");
    drawerPanel.focus({ preventScroll: true });
  }

  function closeDrawer() {
    clearActiveSegment({ pause: true });
    drawerRoot.classList.remove("is-open");
    drawerRoot.setAttribute("aria-hidden", "true");
    drawerRoot.hidden = true;
    document.body.classList.remove("has-job-drawer-open");
    drawerContent.innerHTML = '<div class="drawer-loading">请选择一条任务查看详情。</div>';
    if (activeTrigger) {
      activeTrigger.focus({ preventScroll: true });
      activeTrigger = null;
    }
  }

  async function openJobDrawer(link) {
    activeTrigger = link;
    drawerTitle.textContent = "任务详情";
    drawerContent.innerHTML = '<div class="drawer-loading">正在加载任务详情...</div>';
    openDrawerShell();

    const response = await fetch(link.dataset.drawerUrl, {
      method: "GET",
      headers: {
        "X-Requested-With": "XMLHttpRequest",
      },
    });
    drawerContent.innerHTML = await response.text();
  }

  document.addEventListener("click", function (event) {
    const segmentButton = event.target.closest("[data-segment-play]");
    if (segmentButton) {
      event.preventDefault();
      playSegment(segmentButton);
      return;
    }

    if (!drawerRoot) {
      return;
    }

    const closeButton = event.target.closest("[data-drawer-close]");
    if (closeButton) {
      event.preventDefault();
      closeDrawer();
      return;
    }

    const link = event.target.closest("[data-job-drawer-link]");
    if (!link) {
      return;
    }
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
      return;
    }

    event.preventDefault();
    openJobDrawer(link).catch(function () {
      window.location.href = link.href;
    });
  });

  document.addEventListener(
    "error",
    function (event) {
      if (event.target && event.target.matches && event.target.matches("[data-job-audio]")) {
        showAudioError(event.target);
      }
    },
    true,
  );

  document.addEventListener("keydown", function (event) {
    if (drawerRoot && event.key === "Escape" && drawerRoot.classList.contains("is-open")) {
      closeDrawer();
    }
  });

  initUploadForms();
})();

(function () {
  function isFiniteNumber(value) {
    return typeof value === "number" && isFinite(value);
  }

  function integerSecond(value) {
    return isFiniteNumber(value) ? Math.floor(value) : 0;
  }

  function setClass(element, name, enabled) {
    if (!element) {
      return;
    }
    if (element.classList) {
      if (enabled) {
        element.classList.add(name);
      } else {
        element.classList.remove(name);
      }
      return;
    }
    var expression = new RegExp("(^|\\s)" + name + "(?:\\s|$)", "g");
    element.className = enabled
      ? (element.className + " " + name).replace(/^\s+/, "")
      : element.className.replace(expression, " ").replace(/^\s+|\s+$/g, "");
  }

  function findParent(element, attributeName, boundary) {
    var current = element;
    while (current && current !== boundary) {
      if (current.getAttribute && current.getAttribute(attributeName) !== null) {
        return current;
      }
      current = current.parentNode;
    }
    return null;
  }

  function initAnnotationUpload() {
    var form = document.querySelector("[data-annotation-upload-form]");
    if (!form) {
      return;
    }
    var input = form.querySelector("[data-annotation-upload-input]");
    var dropzone = form.querySelector("[data-annotation-upload-dropzone]");
    var name = form.querySelector("[data-annotation-upload-name]");
    var submit = form.querySelector("[data-annotation-upload-submit]");
    if (!input || !dropzone || !name) {
      return;
    }
    function sync() {
      var file = input.files && input.files[0];
      name.textContent = file ? "已选择：" + file.name : "支持 WAV、MP3、M4A、FLAC、OGG、AAC，最大 100MB";
      setClass(dropzone, "has-file", Boolean(file));
    }
    input.addEventListener("change", sync);
    dropzone.addEventListener("dragover", function (event) {
      event.preventDefault();
      setClass(dropzone, "is-dragging", true);
    });
    dropzone.addEventListener("dragleave", function () {
      setClass(dropzone, "is-dragging", false);
    });
    dropzone.addEventListener("drop", function (event) {
      event.preventDefault();
      setClass(dropzone, "is-dragging", false);
      if (!event.dataTransfer || !event.dataTransfer.files.length) {
        return;
      }
      input.files = event.dataTransfer.files;
      sync();
    });
    form.addEventListener("submit", function () {
      if (submit) {
        submit.disabled = true;
        submit.textContent = "上传中...";
      }
    });
    sync();
  }

  function initAnnotationEditorLegacy() {
    var root = document.querySelector("[data-annotation-editor]");
    if (!root) {
      return;
    }
    var form = root.querySelector("[data-annotation-form]");
    var list = root.querySelector("[data-annotation-segment-list]");
    var template = root.querySelector("[data-annotation-segment-template]");
    var audio = root.querySelector("[data-annotation-audio]");
    var durationInput = root.querySelector("[data-annotation-duration]");
    var status = root.querySelector("[data-annotation-playback-status]");
    var timestampList = root.querySelector("[data-annotation-timestamp-list]");
    var segmentCount = root.querySelector("[data-annotation-segment-count]");
    var segmentCountText = root.querySelector("[data-annotation-segment-count-text]");
    var usableInput = root.querySelector("[data-audio-usable]");
    var addButton = root.querySelector("[data-add-annotation-segment]");
    var saveButton = root.querySelector("[data-annotation-save]");
    var overallNoteInput = form ? form.querySelector("[name=\"overall_note\"]") : null;
    var editingStatus = root.querySelector("[data-annotation-editing-status]");
    var editingMessage = root.querySelector("[data-annotation-editing-message]");
    var activeTimeInput = null;
    var timestampItems = [];
    var replayStartSecond = null;
    var replayEndSecond = null;
    if (!form || !list || !template || !audio || !segmentCount || !usableInput) {
      return;
    }

    function getSegmentCards() {
      return list.querySelectorAll("[data-annotation-segment]");
    }

    function hasSuffix(value, suffix) {
      return value.slice(-suffix.length) === suffix;
    }

    function containsValue(values, expected) {
      var index;
      for (index = 0; index < values.length; index += 1) {
        if (values[index] === expected) {
          return true;
        }
      }
      return false;
    }

    function refreshSegments() {
      var cards = getSegmentCards();
      var index;
      for (index = 0; index < cards.length; index += 1) {
        var number = cards[index].querySelector("[data-annotation-segment-number]");
        var fields = cards[index].querySelectorAll("[name]");
        var fieldIndex;
        if (number) {
          number.textContent = String(index + 1);
        }
        for (fieldIndex = 0; fieldIndex < fields.length; fieldIndex += 1) {
          fields[fieldIndex].name = fields[fieldIndex].name.replace(/segment_\d+_/, "segment_" + index + "_");
        }
      }
      segmentCount.value = String(cards.length);
      if (segmentCountText) {
        segmentCountText.textContent = "当前 " + cards.length + " 个片段";
      }
    }

    function syncUsableState() {
      var enabled = usableInput.checked;
      var fields = list.querySelectorAll(
        "input, select, textarea, [data-set-annotation-time], [data-save-annotation-segment]"
      );
      var index;
      setClass(list, "is-disabled", !enabled);
      for (index = 0; index < fields.length; index += 1) {
        fields[index].disabled = !enabled;
      }
      if (addButton) {
        addButton.disabled = !enabled;
      }
    }

    function setSegmentStaged(card, staged, message) {
      if (!card) {
        return;
      }
      card.setAttribute("data-segment-staged", staged ? "1" : "0");
      setClass(card, "is-staged", staged);
      var state = card.querySelector("[data-annotation-segment-state]");
      if (state) {
        state.textContent = message || (staged ? "已暂存" : "未暂存");
      }
    }

    function markSegmentDirty(card) {
      if (card && card.getAttribute("data-segment-staged") === "1") {
        setSegmentStaged(card, false, "已修改，需重新保存");
      }
    }

    function stageSegment(card) {
      var startInput = getTimeInput(card, "start");
      var endInput = getTimeInput(card, "end");
      var start = startInput ? Number(startInput.value) : NaN;
      var end = endInput ? Number(endInput.value) : NaN;
      if (!startInput || !endInput || startInput.value === "" || endInput.value === "") {
        window.alert("请先填写该片段的开始和结束时间。");
        return false;
      }
      if (!isFiniteNumber(start) || !isFiniteNumber(end) || Math.floor(start) !== start || Math.floor(end) !== end) {
        window.alert("片段开始和结束时间必须是整数秒。");
        return false;
      }
      if (start < 0 || end <= start) {
        window.alert("片段结束时间必须大于开始时间。");
        return false;
      }
      var fields = card.querySelectorAll("[name]");
      var hasPrimary = false;
      var index;
      for (index = 0; index < fields.length; index += 1) {
        if (hasSuffix(fields[index].name, "_primary") && fields[index].checked) {
          hasPrimary = true;
          break;
        }
      }
      if (!hasPrimary) {
        window.alert("请为该片段选择一个主情绪硬标签。");
        return false;
      }
      setClass(card, "is-new", false);
      setSegmentStaged(card, true, "已暂存");
      return true;
    }

    function fillSegmentCard(card, values) {
      if (!values) {
        return;
      }
      var fields = card.querySelectorAll("[name]");
      var index;
      for (index = 0; index < fields.length; index += 1) {
        var field = fields[index];
        var name = field.name;
        if (hasSuffix(name, "_start")) {
          field.value = values.start;
        } else if (hasSuffix(name, "_end")) {
          field.value = values.end;
        } else if (hasSuffix(name, "_speaker")) {
          field.value = values.speaker;
        } else if (hasSuffix(name, "_primary")) {
          field.checked = field.value === values.primary;
        } else if (hasSuffix(name, "_secondary")) {
          field.checked = containsValue(values.secondary, field.value);
        } else if (hasSuffix(name, "_confidence")) {
          field.value = values.confidence;
        } else if (hasSuffix(name, "_overlap")) {
          field.checked = values.overlap;
        } else if (hasSuffix(name, "_needs_review")) {
          field.checked = values.needsReview;
        } else if (hasSuffix(name, "_note")) {
          field.value = values.note;
        }
      }
    }

    function addSegment(values, reveal) {
      var index = getSegmentCards().length;
      var wrapper = document.createElement("div");
      wrapper.innerHTML = template.innerHTML.split("__INDEX__").join(String(index));
      var card = wrapper.firstChild;
      while (card && card.nodeType !== 1) {
        card = card.nextSibling;
      }
      if (!card) {
        return;
      }
      list.appendChild(card);
      fillSegmentCard(card, values);
      setSegmentStaged(card, Boolean(values), values ? "已从记录载入" : "未暂存");
      refreshSegments();
      syncUsableState();
      if (reveal) {
        setClass(card, "is-new", true);
        setTimeout(function () {
          if (card.scrollIntoView) {
            card.scrollIntoView(false);
          }
          setTimeout(function () {
            setClass(card, "is-new", false);
          }, 1400);
        }, 0);
      }
      return card;
    }

    function removeAllSegments() {
      while (list.firstChild) {
        list.removeChild(list.firstChild);
      }
      activeTimeInput = null;
      refreshSegments();
    }

    function readHistorySegment(segment) {
      var secondaryValue = segment.getAttribute("data-secondary-emotions") || "";
      return {
        start: segment.getAttribute("data-start-second") || "0",
        end: segment.getAttribute("data-end-second") || "0",
        primary: segment.getAttribute("data-primary-emotion") || "Other",
        secondary: secondaryValue ? secondaryValue.split(",") : [],
        speaker: segment.getAttribute("data-speaker-label") || "",
        overlap: segment.getAttribute("data-overlap") === "1",
        confidence: segment.getAttribute("data-confidence") || "3",
        needsReview: segment.getAttribute("data-needs-review") === "1",
        note: segment.getAttribute("data-segment-note") || ""
      };
    }

    function clearEditingStatus() {
      if (editingStatus) {
        setClass(editingStatus, "is-visible", false);
      }
      if (editingMessage) {
        editingMessage.textContent = "";
      }
    }

    function resetAnnotationTool() {
      removeAllSegments();
      usableInput.checked = true;
      if (overallNoteInput) {
        overallNoteInput.value = "";
      }
      timestampItems = [];
      renderTimestamps();
      addSegment();
      clearEditingStatus();
      syncUsableState();
    }

    function loadHistoryRecord(record) {
      var recordSegments = record.querySelectorAll("[data-annotation-history-segment]");
      var isUsable = record.getAttribute("data-audio-usable") === "1";
      var index;
      audio.pause();
      replayStartSecond = null;
      replayEndSecond = null;
      removeAllSegments();
      usableInput.checked = isUsable;
      if (overallNoteInput) {
        overallNoteInput.value = record.getAttribute("data-overall-note") || "";
      }
      for (index = 0; index < recordSegments.length; index += 1) {
        addSegment(readHistorySegment(recordSegments[index]));
      }
      if (isUsable && !recordSegments.length) {
        addSegment();
      }
      syncUsableState();
      if (editingStatus) {
        setClass(editingStatus, "is-visible", true);
      }
      if (editingMessage) {
        editingMessage.textContent = "正在基于“" + (record.getAttribute("data-record-label") || "历史记录") + "”修改；保存后生成新版本。";
      }
      if (document.documentElement.clientWidth <= 1180 && form.scrollIntoView) {
        form.scrollIntoView(true);
      }
    }

    function setPlaybackStatus(message) {
      if (!status) {
        return;
      }
      if (message) {
        status.textContent = message;
        return;
      }
      var current = integerSecond(Number(audio.currentTime));
      if (replayEndSecond !== null) {
        status.textContent = "正在回听标注片段：" + current + " 秒 / 结束 " + replayEndSecond + " 秒";
        return;
      }
      var duration = Number(audio.duration);
      status.textContent = isFiniteNumber(duration)
        ? "当前 " + current + " 秒 / 音频时长 " + integerSecond(duration) + " 秒"
        : "正在读取音频时长...";
    }

    function renderTimestamps() {
      if (!timestampList) {
        return;
      }
      while (timestampList.firstChild) {
        timestampList.removeChild(timestampList.firstChild);
      }
      if (!timestampItems.length) {
        var empty = document.createElement("span");
        empty.className = "annotation-timestamp-empty";
        empty.setAttribute("data-annotation-timestamp-empty", "");
        empty.appendChild(document.createTextNode("尚未插入时间戳"));
        timestampList.appendChild(empty);
        return;
      }
      var index;
      for (index = 0; index < timestampItems.length; index += 1) {
        var marker = document.createElement("button");
        var label = document.createElement("span");
        var value = document.createElement("span");
        marker.type = "button";
        marker.className = "annotation-timestamp-marker";
        marker.setAttribute("data-annotation-timestamp-second", String(timestampItems[index].second));
        marker.setAttribute("data-annotation-timestamp-index", String(index + 1));
        label.className = "annotation-timestamp-label";
        label.appendChild(document.createTextNode("时间戳 #" + (index + 1)));
        value.className = "annotation-timestamp-value";
        value.appendChild(document.createTextNode(timestampItems[index].second + " 秒"));
        marker.appendChild(label);
        marker.appendChild(value);
        timestampList.appendChild(marker);
      }
    }

    function addTimestamp(second) {
      var value = integerSecond(second);
      timestampItems.push({ second: value });
      renderTimestamps();
      setPlaybackStatus("已插入第 " + timestampItems.length + " 个时间戳：" + value + " 秒");
    }

    function getTimeInput(card, kind) {
      var fields = card ? card.getElementsByTagName("input") : [];
      var suffix = "_" + kind;
      var index;
      for (index = 0; index < fields.length; index += 1) {
        if (fields[index].name.slice(-suffix.length) === suffix) {
          return fields[index];
        }
      }
      return null;
    }

    function writeSecond(input, second) {
      if (!input || input.disabled) {
        return;
      }
      input.value = String(integerSecond(second));
      markSegmentDirty(findParent(input, "data-annotation-segment", root));
      activeTimeInput = input;
      input.focus();
    }

    audio.addEventListener("loadedmetadata", function () {
      var duration = Number(audio.duration);
      if (durationInput && isFiniteNumber(duration)) {
        durationInput.value = String(integerSecond(duration));
      }
      setPlaybackStatus();
    });
    audio.addEventListener("timeupdate", function () {
      if (replayEndSecond !== null && Number(audio.currentTime) >= replayEndSecond) {
        var completedStart = replayStartSecond;
        var completedEnd = replayEndSecond;
        audio.pause();
        audio.currentTime = completedEnd;
        replayStartSecond = null;
        replayEndSecond = null;
        setPlaybackStatus("回听完成：" + completedStart + "-" + completedEnd + " 秒");
        return;
      }
      setPlaybackStatus();
    });
    audio.addEventListener("pause", function () {
      if (replayEndSecond !== null && Number(audio.currentTime) < replayEndSecond - 0.1) {
        var pausedAt = integerSecond(Number(audio.currentTime));
        replayStartSecond = null;
        replayEndSecond = null;
        setPlaybackStatus("已在 " + pausedAt + " 秒暂停回听");
      }
    });
    audio.addEventListener("error", function () {
      setPlaybackStatus("音频加载失败，请确认源文件仍在服务器上且浏览器支持该格式。");
    });
    list.addEventListener("focusin", function (event) {
      var field = event.target;
      if (field && field.name && (field.name.slice(-6) === "_start" || field.name.slice(-4) === "_end")) {
        activeTimeInput = field;
      }
    });
    list.addEventListener("input", function (event) {
      markSegmentDirty(findParent(event.target, "data-annotation-segment", root));
    });

    root.addEventListener("click", function (event) {
      var addSegmentButton = findParent(event.target, "data-add-annotation-segment", root);
      if (addSegmentButton) {
        addSegment(null, true);
        return;
      }
      var saveSegmentButton = findParent(event.target, "data-save-annotation-segment", root);
      if (saveSegmentButton) {
        var cardToStage = findParent(saveSegmentButton, "data-annotation-segment", root);
        stageSegment(cardToStage);
        return;
      }
      var editButton = findParent(event.target, "data-edit-annotation-record", root);
      if (editButton) {
        var historyRecord = findParent(editButton, "data-annotation-history-record", root);
        if (historyRecord) {
          loadHistoryRecord(historyRecord);
        }
        return;
      }
      var clearButton = findParent(event.target, "data-clear-annotation-tool", root);
      if (clearButton) {
        resetAnnotationTool();
        return;
      }
      var playbackButton = findParent(event.target, "data-play-annotation-segment", root);
      if (playbackButton) {
        var playbackStart = Number(playbackButton.getAttribute("data-start-second"));
        var playbackEnd = Number(playbackButton.getAttribute("data-end-second"));
        if (!isFiniteNumber(playbackStart) || !isFiniteNumber(playbackEnd) || playbackEnd <= playbackStart) {
          setPlaybackStatus("该条标注的起止时间无效，无法回听。");
          return;
        }
        replayStartSecond = playbackStart;
        replayEndSecond = playbackEnd;
        audio.currentTime = playbackStart;
        var playResult = audio.play();
        if (playResult && typeof playResult.catch === "function") {
          playResult.catch(function () {
            replayStartSecond = null;
            replayEndSecond = null;
            setPlaybackStatus("浏览器未允许自动播放，请再次点击回听按钮。");
          });
        }
        setPlaybackStatus();
        return;
      }
      var removeButton = findParent(event.target, "data-remove-annotation-segment", root);
      if (removeButton) {
        var cardToRemove = findParent(removeButton, "data-annotation-segment", root);
        if (cardToRemove && cardToRemove.parentNode) {
          if (activeTimeInput && cardToRemove.contains(activeTimeInput)) {
            activeTimeInput = null;
          }
          cardToRemove.parentNode.removeChild(cardToRemove);
          refreshSegments();
        }
        return;
      }
      var timeButton = findParent(event.target, "data-set-annotation-time", root);
      if (timeButton) {
        var card = findParent(timeButton, "data-annotation-segment", root);
        writeSecond(getTimeInput(card, timeButton.getAttribute("data-set-annotation-time")), Number(audio.currentTime));
        return;
      }
      var timestampMarker = findParent(event.target, "data-annotation-timestamp-second", root);
      if (timestampMarker) {
        var second = Number(timestampMarker.getAttribute("data-annotation-timestamp-second"));
        replayStartSecond = null;
        replayEndSecond = null;
        audio.currentTime = second;
        writeSecond(activeTimeInput, second);
        if (!activeTimeInput) {
          setPlaybackStatus("已定位到 " + second + " 秒；选择开始或结束输入框后可再点击时间戳填入。");
        }
        return;
      }
      var insertButton = findParent(event.target, "data-add-annotation-timestamp", root);
      if (insertButton) {
        addTimestamp(Number(audio.currentTime));
      }
    });

    root.addEventListener("change", function (event) {
      var field = event.target;
      var changedCard = findParent(field, "data-annotation-segment", root);
      markSegmentDirty(changedCard);
      if (!field || field.type !== "checkbox" || field.name.slice(-10) !== "_secondary" || !field.checked) {
        return;
      }
      var card = changedCard;
      var checkboxes = card ? card.getElementsByTagName("input") : [];
      var selected = 0;
      var index;
      for (index = 0; index < checkboxes.length; index += 1) {
        if (checkboxes[index].name.slice(-10) === "_secondary" && checkboxes[index].checked) {
          selected += 1;
        }
      }
      if (selected > 2) {
        field.checked = false;
        window.alert("每个片段最多选择两个辅助情绪；请保留最明显的两个。");
      }
    });

    usableInput.addEventListener("change", function () {
      if (usableInput.checked && !getSegmentCards().length) {
        addSegment(null, true);
      }
      syncUsableState();
    });
    form.addEventListener("submit", function (event) {
      if (usableInput.checked) {
        var cards = getSegmentCards();
        var cardIndex;
        if (!cards.length) {
          event.preventDefault();
          window.alert("请先新增并保存至少一个情绪片段。");
          return;
        }
        for (cardIndex = 0; cardIndex < cards.length; cardIndex += 1) {
          if (cards[cardIndex].getAttribute("data-segment-staged") !== "1") {
            event.preventDefault();
            window.alert("请先点击第 " + (cardIndex + 1) + " 个片段中的“保存片段”，再保存本次标注。");
            setClass(cards[cardIndex], "is-new", true);
            if (cards[cardIndex].scrollIntoView) {
              cards[cardIndex].scrollIntoView(false);
            }
            return;
          }
        }
      }
      if (saveButton) {
        saveButton.disabled = true;
        saveButton.textContent = "正在保存...";
      }
    });
    resetAnnotationTool();
  }

  function initAnnotationEditor() {
    var root = document.querySelector("[data-annotation-editor]");
    if (!root) {
      return;
    }
    var form = root.querySelector("[data-annotation-form]");
    var editorList = root.querySelector("[data-annotation-segment-list]");
    var template = root.querySelector("[data-annotation-segment-template]");
    var audio = root.querySelector("[data-annotation-audio]");
    var durationInput = root.querySelector("[data-annotation-duration]");
    var playbackStatus = root.querySelector("[data-annotation-playback-status]");
    var timestampList = root.querySelector("[data-annotation-timestamp-list]");
    var segmentCount = root.querySelector("[data-annotation-segment-count]");
    var draftFields = root.querySelector("[data-annotation-draft-fields]");
    var draftList = root.querySelector("[data-annotation-draft-list]");
    var draftEmpty = root.querySelector("[data-annotation-draft-empty]");
    var draftCount = root.querySelector("[data-annotation-draft-count]");
    var draftCountText = root.querySelector("[data-annotation-segment-count-text]");
    var editorTitle = root.querySelector("[data-annotation-editor-title]");
    var usableInput = root.querySelector("[data-audio-usable]");
    var overallNoteInput = form ? form.querySelector("[name=\"overall_note\"]") : null;
    var finalSaveButton = root.querySelector("[data-annotation-save]");
    var draftSegments = [];
    var editingDraftIndex = -1;
    var editorDirty = false;
    var activeTimeInput = null;
    var timestampItems = [];
    var replayStartSecond = null;
    var replayEndSecond = null;
    if (
      !form || !editorList || !template || !audio || !segmentCount || !draftFields ||
      !draftList || !usableInput
    ) {
      return;
    }

    function hasSuffix(value, suffix) {
      return value.slice(-suffix.length) === suffix;
    }

    function getEditorCard() {
      return editorList.querySelector("[data-annotation-segment]");
    }

    function getEditorFields() {
      var card = getEditorCard();
      return card ? card.querySelectorAll("[name]") : [];
    }

    function findEditorField(suffix, expectedValue) {
      var fields = getEditorFields();
      var index;
      for (index = 0; index < fields.length; index += 1) {
        if (
          hasSuffix(fields[index].name, suffix) &&
          (typeof expectedValue === "undefined" || fields[index].value === expectedValue)
        ) {
          return fields[index];
        }
      }
      return null;
    }

    function getOptionLabel(field) {
      var labels = field && field.parentNode ? field.parentNode.getElementsByTagName("span") : [];
      return labels.length ? labels[0].textContent : (field ? field.value : "");
    }

    function createEditorCard() {
      while (editorList.firstChild) {
        editorList.removeChild(editorList.firstChild);
      }
      var wrapper = document.createElement("div");
      wrapper.innerHTML = template.innerHTML.split("__INDEX__").join("0");
      var card = wrapper.firstChild;
      while (card && card.nodeType !== 1) {
        card = card.nextSibling;
      }
      if (!card) {
        return null;
      }
      editorList.appendChild(card);
      return card;
    }

    function setEditorState(message, buttonLabel) {
      var card = getEditorCard();
      var state = card ? card.querySelector("[data-annotation-segment-state]") : null;
      var button = card ? card.querySelector("[data-save-annotation-segment]") : null;
      var number = card ? card.querySelector("[data-annotation-segment-number]") : null;
      if (state) {
        state.textContent = message;
      }
      if (button) {
        button.textContent = buttonLabel;
      }
      if (number) {
        number.textContent = editingDraftIndex >= 0 ? String(editingDraftIndex + 1) : "新";
      }
      if (editorTitle) {
        editorTitle.textContent = editingDraftIndex >= 0
          ? "编辑本次片段 " + (editingDraftIndex + 1)
          : "新增片段";
      }
    }

    function syncEditorEnabled() {
      var enabled = usableInput.checked;
      var card = getEditorCard();
      var controls = card ? card.querySelectorAll("input, select, textarea, button") : [];
      var index;
      setClass(editorList, "is-disabled", !enabled);
      for (index = 0; index < controls.length; index += 1) {
        controls[index].disabled = !enabled;
      }
    }

    function clearEditor() {
      createEditorCard();
      editingDraftIndex = -1;
      editorDirty = false;
      activeTimeInput = null;
      setEditorState("尚未保存", "保存片段");
      syncEditorEnabled();
    }

    function setFieldValue(suffix, value) {
      var field = findEditorField(suffix);
      if (field) {
        field.value = value;
      }
    }

    function fillEditor(segment, index) {
      clearEditor();
      editingDraftIndex = index;
      setFieldValue("_start", segment.start);
      setFieldValue("_end", segment.end);
      setFieldValue("_confidence", segment.confidence);
      setFieldValue("_note", segment.note);
      var primary = findEditorField("_primary", segment.primary);
      if (primary) {
        primary.checked = true;
      }
      var secondaryIndex;
      for (secondaryIndex = 0; secondaryIndex < segment.secondary.length; secondaryIndex += 1) {
        var secondary = findEditorField("_secondary", segment.secondary[secondaryIndex]);
        if (secondary) {
          secondary.checked = true;
        }
      }
      var overlap = findEditorField("_overlap");
      var needsReview = findEditorField("_needs_review");
      if (overlap) {
        overlap.checked = segment.overlap;
      }
      if (needsReview) {
        needsReview.checked = segment.needsReview;
      }
      editorDirty = false;
      setEditorState("编辑本次片段", "更新片段");
      syncEditorEnabled();
    }

    function markEditorDirty() {
      if (!usableInput.checked) {
        return;
      }
      editorDirty = true;
      setEditorState(
        editingDraftIndex >= 0 ? "修改尚未保存" : "尚未保存",
        editingDraftIndex >= 0 ? "更新片段" : "保存片段"
      );
    }

    function collectEditorSegment() {
      var startInput = findEditorField("_start");
      var endInput = findEditorField("_end");
      var start = startInput ? Number(startInput.value) : NaN;
      var end = endInput ? Number(endInput.value) : NaN;
      if (!startInput || !endInput || startInput.value === "" || endInput.value === "") {
        window.alert("请先填写片段的开始和结束时间。");
        return null;
      }
      if (!isFiniteNumber(start) || !isFiniteNumber(end) || Math.floor(start) !== start || Math.floor(end) !== end) {
        window.alert("片段开始和结束时间必须是整数秒。");
        return null;
      }
      if (start < 0 || end <= start) {
        window.alert("片段结束时间必须大于开始时间。");
        return null;
      }
      var duration = Number(audio.duration);
      if (isFiniteNumber(duration) && end > duration + 0.25) {
        window.alert("片段结束时间超出了音频时长。");
        return null;
      }
      var fields = getEditorFields();
      var primary = "";
      var primaryLabel = "";
      var secondary = [];
      var secondaryLabels = [];
      var fieldIndex;
      for (fieldIndex = 0; fieldIndex < fields.length; fieldIndex += 1) {
        if (hasSuffix(fields[fieldIndex].name, "_primary") && fields[fieldIndex].checked) {
          primary = fields[fieldIndex].value;
          primaryLabel = getOptionLabel(fields[fieldIndex]);
        }
        if (hasSuffix(fields[fieldIndex].name, "_secondary") && fields[fieldIndex].checked) {
          secondary.push(fields[fieldIndex].value);
          secondaryLabels.push(getOptionLabel(fields[fieldIndex]));
        }
      }
      if (!primary) {
        window.alert("请为片段选择一个主情绪硬标签。");
        return null;
      }
      return {
        start: String(start),
        end: String(end),
        primary: primary,
        primaryLabel: primaryLabel,
        secondary: secondary,
        secondaryLabels: secondaryLabels,
        confidence: (findEditorField("_confidence") || {}).value || "3",
        overlap: Boolean((findEditorField("_overlap") || {}).checked),
        needsReview: Boolean((findEditorField("_needs_review") || {}).checked),
        note: (findEditorField("_note") || {}).value || ""
      };
    }

    function appendTextElement(parent, tagName, className, value) {
      var element = document.createElement(tagName);
      if (className) {
        element.className = className;
      }
      element.appendChild(document.createTextNode(value));
      parent.appendChild(element);
      return element;
    }

    function createDraftButton(label, attributeName, index, className) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = className;
      button.setAttribute(attributeName, "");
      button.setAttribute("data-draft-index", String(index));
      button.appendChild(document.createTextNode(label));
      return button;
    }

    function renderDraftSegments() {
      while (draftList.firstChild) {
        draftList.removeChild(draftList.firstChild);
      }
      var index;
      for (index = 0; index < draftSegments.length; index += 1) {
        var segment = draftSegments[index];
        var item = document.createElement("article");
        var heading = document.createElement("div");
        var description = document.createElement("div");
        var actions = document.createElement("div");
        item.className = "annotation-history-item annotation-draft-item";
        heading.className = "annotation-history-item-heading";
        description.className = "annotation-draft-description";
        actions.className = "annotation-history-item-actions";
        appendTextElement(description, "strong", "", "片段 " + (index + 1) + " · " + segment.primaryLabel);
        var details = segment.start + "-" + segment.end + " 秒 · 把握程度 " + segment.confidence + "/5";
        appendTextElement(description, "span", "", details);
        if (segment.secondaryLabels.length) {
          appendTextElement(description, "span", "", "辅助情绪：" + segment.secondaryLabels.join("、"));
        }
        actions.appendChild(createDraftButton("回听", "data-play-draft-segment", index, "plain-button"));
        actions.appendChild(createDraftButton("编辑", "data-edit-draft-segment", index, "plain-button"));
        actions.appendChild(createDraftButton("删除", "data-delete-draft-segment", index, "text-button"));
        heading.appendChild(description);
        heading.appendChild(actions);
        item.appendChild(heading);
        draftList.appendChild(item);
      }
      var countLabel = "本次已保存 " + draftSegments.length + " 个片段";
      if (draftCount) {
        draftCount.textContent = countLabel;
      }
      if (draftCountText) {
        draftCountText.textContent = "本次已保存 " + draftSegments.length + " 个";
      }
      if (draftEmpty) {
        draftEmpty.style.display = draftSegments.length ? "none" : "";
      }
      segmentCount.value = String(draftSegments.length);
    }

    function saveCurrentSegment() {
      var segment = collectEditorSegment();
      if (!segment) {
        return;
      }
      if (editingDraftIndex >= 0 && editingDraftIndex < draftSegments.length) {
        draftSegments[editingDraftIndex] = segment;
      } else {
        draftSegments.push(segment);
      }
      renderDraftSegments();
      clearEditor();
    }

    function appendHiddenField(name, value) {
      var input = document.createElement("input");
      input.type = "hidden";
      input.name = name;
      input.value = value;
      draftFields.appendChild(input);
    }

    function buildDraftFields() {
      while (draftFields.firstChild) {
        draftFields.removeChild(draftFields.firstChild);
      }
      var index;
      for (index = 0; index < draftSegments.length; index += 1) {
        var segment = draftSegments[index];
        var prefix = "segment_" + index + "_";
        appendHiddenField(prefix + "start", segment.start);
        appendHiddenField(prefix + "end", segment.end);
        appendHiddenField(prefix + "primary", segment.primary);
        var secondaryIndex;
        for (secondaryIndex = 0; secondaryIndex < segment.secondary.length; secondaryIndex += 1) {
          appendHiddenField(prefix + "secondary", segment.secondary[secondaryIndex]);
        }
        appendHiddenField(prefix + "confidence", segment.confidence);
        appendHiddenField(prefix + "note", segment.note);
        if (segment.overlap) {
          appendHiddenField(prefix + "overlap", "1");
        }
        if (segment.needsReview) {
          appendHiddenField(prefix + "needs_review", "1");
        }
      }
      segmentCount.value = String(draftSegments.length);
    }

    function setPlaybackStatus(message) {
      if (!playbackStatus) {
        return;
      }
      if (message) {
        playbackStatus.textContent = message;
        return;
      }
      var current = integerSecond(Number(audio.currentTime));
      if (replayEndSecond !== null) {
        playbackStatus.textContent = "正在回听本次片段：" + current + " 秒 / 结束 " + replayEndSecond + " 秒";
        return;
      }
      var duration = Number(audio.duration);
      playbackStatus.textContent = isFiniteNumber(duration)
        ? "当前 " + current + " 秒 / 音频时长 " + integerSecond(duration) + " 秒"
        : "正在读取音频时长...";
    }

    function renderTimestamps() {
      if (!timestampList) {
        return;
      }
      while (timestampList.firstChild) {
        timestampList.removeChild(timestampList.firstChild);
      }
      if (!timestampItems.length) {
        appendTextElement(timestampList, "span", "annotation-timestamp-empty", "尚未插入时间戳");
        return;
      }
      var index;
      for (index = 0; index < timestampItems.length; index += 1) {
        var marker = document.createElement("button");
        marker.type = "button";
        marker.className = "annotation-timestamp-marker";
        marker.setAttribute("data-annotation-timestamp-second", String(timestampItems[index]));
        appendTextElement(marker, "span", "annotation-timestamp-label", "时间戳 #" + (index + 1));
        appendTextElement(marker, "span", "annotation-timestamp-value", timestampItems[index] + " 秒");
        timestampList.appendChild(marker);
      }
    }

    function addTimestamp() {
      var value = integerSecond(Number(audio.currentTime));
      timestampItems.push(value);
      renderTimestamps();
      setPlaybackStatus("已插入第 " + timestampItems.length + " 个时间戳：" + value + " 秒");
    }

    function writeCurrentSecond(input) {
      if (!input || input.disabled) {
        return;
      }
      input.value = String(integerSecond(Number(audio.currentTime)));
      activeTimeInput = input;
      markEditorDirty();
      input.focus();
    }

    function playDraftSegment(index) {
      if (index < 0 || index >= draftSegments.length) {
        return;
      }
      replayStartSecond = Number(draftSegments[index].start);
      replayEndSecond = Number(draftSegments[index].end);
      audio.currentTime = replayStartSecond;
      var playResult = audio.play();
      if (playResult && typeof playResult.catch === "function") {
        playResult.catch(function () {
          replayStartSecond = null;
          replayEndSecond = null;
          setPlaybackStatus("浏览器未允许播放，请再次点击回听。");
        });
      }
      setPlaybackStatus();
    }

    audio.addEventListener("loadedmetadata", function () {
      var duration = Number(audio.duration);
      if (durationInput && isFiniteNumber(duration)) {
        durationInput.value = String(integerSecond(duration));
      }
      setPlaybackStatus();
    });
    audio.addEventListener("timeupdate", function () {
      if (replayEndSecond !== null && Number(audio.currentTime) >= replayEndSecond) {
        var completedStart = replayStartSecond;
        var completedEnd = replayEndSecond;
        audio.pause();
        audio.currentTime = completedEnd;
        replayStartSecond = null;
        replayEndSecond = null;
        setPlaybackStatus("回听完成：" + completedStart + "-" + completedEnd + " 秒");
        return;
      }
      setPlaybackStatus();
    });
    audio.addEventListener("pause", function () {
      if (replayEndSecond !== null && Number(audio.currentTime) < replayEndSecond - 0.1) {
        replayStartSecond = null;
        replayEndSecond = null;
        setPlaybackStatus("已暂停回听");
      }
    });
    audio.addEventListener("error", function () {
      setPlaybackStatus("音频加载失败，请确认源文件仍在服务器上且浏览器支持该格式。");
    });

    editorList.addEventListener("focusin", function (event) {
      var field = event.target;
      if (field && field.name && (hasSuffix(field.name, "_start") || hasSuffix(field.name, "_end"))) {
        activeTimeInput = field;
      }
    });
    editorList.addEventListener("input", markEditorDirty);

    root.addEventListener("click", function (event) {
      var saveSegmentButton = findParent(event.target, "data-save-annotation-segment", root);
      if (saveSegmentButton) {
        saveCurrentSegment();
        return;
      }
      var clearButton = findParent(event.target, "data-clear-current-segment", root);
      if (clearButton) {
        clearEditor();
        return;
      }
      var playButton = findParent(event.target, "data-play-draft-segment", root);
      if (playButton) {
        playDraftSegment(Number(playButton.getAttribute("data-draft-index")));
        return;
      }
      var editButton = findParent(event.target, "data-edit-draft-segment", root);
      if (editButton) {
        var editIndex = Number(editButton.getAttribute("data-draft-index"));
        if (editIndex >= 0 && editIndex < draftSegments.length) {
          fillEditor(draftSegments[editIndex], editIndex);
        }
        return;
      }
      var deleteButton = findParent(event.target, "data-delete-draft-segment", root);
      if (deleteButton) {
        var deleteIndex = Number(deleteButton.getAttribute("data-draft-index"));
        if (deleteIndex >= 0 && deleteIndex < draftSegments.length && window.confirm("确定删除本次标注中的片段 " + (deleteIndex + 1) + " 吗？")) {
          draftSegments.splice(deleteIndex, 1);
          if (editingDraftIndex === deleteIndex) {
            clearEditor();
          } else if (editingDraftIndex > deleteIndex) {
            editingDraftIndex -= 1;
            setEditorState("编辑本次片段", "更新片段");
          }
          renderDraftSegments();
        }
        return;
      }
      var timeButton = findParent(event.target, "data-set-annotation-time", root);
      if (timeButton) {
        writeCurrentSecond(findEditorField("_" + timeButton.getAttribute("data-set-annotation-time")));
        return;
      }
      var timestampMarker = findParent(event.target, "data-annotation-timestamp-second", root);
      if (timestampMarker) {
        var second = Number(timestampMarker.getAttribute("data-annotation-timestamp-second"));
        replayStartSecond = null;
        replayEndSecond = null;
        audio.currentTime = second;
        if (activeTimeInput) {
          activeTimeInput.value = String(integerSecond(second));
          markEditorDirty();
          activeTimeInput.focus();
        }
        return;
      }
      var timestampButton = findParent(event.target, "data-add-annotation-timestamp", root);
      if (timestampButton) {
        addTimestamp();
      }
    });

    root.addEventListener("change", function (event) {
      var field = event.target;
      var card = findParent(field, "data-annotation-segment", root);
      if (card) {
        markEditorDirty();
      }
      if (!field || field.type !== "checkbox" || !field.name || !hasSuffix(field.name, "_secondary") || !field.checked) {
        return;
      }
      var fields = getEditorFields();
      var selected = 0;
      var index;
      for (index = 0; index < fields.length; index += 1) {
        if (hasSuffix(fields[index].name, "_secondary") && fields[index].checked) {
          selected += 1;
        }
      }
      if (selected > 2) {
        field.checked = false;
        window.alert("每个片段最多选择两个辅助情绪。");
      }
    });

    usableInput.addEventListener("change", function () {
      if (!usableInput.checked && (draftSegments.length || editorDirty)) {
        usableInput.checked = true;
        window.alert("本次标注已有片段或未保存内容；请先删除片段并清空当前编辑器，再标记音频不可用。");
      }
      syncEditorEnabled();
    });

    form.addEventListener("submit", function (event) {
      if (usableInput.checked) {
        if (editorDirty) {
          event.preventDefault();
          window.alert("右侧还有未保存的片段内容，请先点击“保存片段”。");
          return;
        }
        if (!draftSegments.length) {
          event.preventDefault();
          window.alert("本次标注至少需要保存一个片段。");
          return;
        }
      }
      buildDraftFields();
      var editorControls = getEditorCard() ? getEditorCard().querySelectorAll("input, select, textarea, button") : [];
      var controlIndex;
      for (controlIndex = 0; controlIndex < editorControls.length; controlIndex += 1) {
        editorControls[controlIndex].disabled = true;
      }
      if (finalSaveButton) {
        finalSaveButton.disabled = true;
        finalSaveButton.textContent = "正在保存...";
      }
    });

    usableInput.checked = true;
    if (overallNoteInput) {
      overallNoteInput.value = "";
    }
    clearEditor();
    renderDraftSegments();
    renderTimestamps();
  }

  initAnnotationUpload();
  initAnnotationEditor();
})();
