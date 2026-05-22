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
