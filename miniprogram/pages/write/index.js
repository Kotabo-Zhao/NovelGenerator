const api = require("../../utils/api");

Page({
  data: {
    novelId: "",
    novel: null,
    chapter: 1,
    content: "",
    draftContent: "",
    editing: false,
    draftSaving: false,
    contentOffset: 0,
    totalContent: 0,
    hasMoreContent: false,
    loading: false,
    readingLoading: false,
    jobId: "",
    jobStatus: "",
    jobProgress: 0,
    jobPhase: "",
    jobStatusText: "",
    totalChapters: 0,
    completedChapters: 0,
    novelProgress: 0,
    canNext: false,
    chapterSkeletons: [1, 2, 3, 4, 5, 6],
    error: ""
  },

  onShow() {
    const novelId = wx.getStorageSync("activeNovelId") || getApp().globalData.activeNovelId || "";
    const jobId = wx.getStorageSync("activeJobId") || "";
    if (novelId && novelId !== this.data.novelId) {
      this.setData({
        novelId,
        novel: null,
        content: "",
        draftContent: "",
        editing: false,
        contentOffset: 0,
        totalContent: 0,
        hasMoreContent: false,
        chapter: 1,
        jobId,
        jobStatus: "",
        jobStatusText: "",
        error: ""
      });
      this.loadNovel();
      return;
    }
    if (jobId !== this.data.jobId) {
      this.setData({
        jobId,
        jobStatus: jobId ? this.data.jobStatus : "",
        jobStatusText: jobId ? this.data.jobStatusText : ""
      });
    }
    if (novelId && !this.data.novel) this.loadNovel();
    if (jobId) this.pollJob();
  },

  onHide() {
    if (this._pollTimer) clearTimeout(this._pollTimer);
    this._pollTimer = null;
  },

  onUnload() {
    if (this._pollTimer) clearTimeout(this._pollTimer);
    this._pollTimer = null;
  },

  async loadNovel() {
    this.setData({ loading: true, error: "" });
    try {
      const result = await api.get(`/v1/novels/${encodeURIComponent(this.data.novelId)}`);
      const novel = result.novel || {};
      const state = novel.state || {};
      const totalChapters = Number(state.total_chapters || 0);
      const completedChapters = Array.isArray(state.completed_chapters) ? state.completed_chapters.length : 0;
      const chapter = Math.max(1, this.data.novel ? this.data.chapter : (state.current_chapter || 1));
      this.setData({
        novel,
        chapter,
        totalChapters,
        completedChapters,
        novelProgress: totalChapters ? Math.min(100, Math.round(completedChapters / totalChapters * 100)) : 0,
        canNext: totalChapters ? chapter < totalChapters : true
      });
      this.loadChapter(chapter);
      if (this.data.jobId) this.pollJob();
    } catch (error) {
      this.setData({ error: error.message || "无法读取作品" });
    } finally {
      this.setData({ loading: false });
    }
  },

  async loadChapter(chapter, append = false) {
    const chapterNum = Number(chapter);
    if (!this.data.novelId || !Number.isInteger(chapterNum) || chapterNum < 1) return;
    const offset = append ? this.data.content.length : 0;
    this.setData({ readingLoading: true, error: "" });
    try {
      const result = await api.get(`/v1/novels/${encodeURIComponent(this.data.novelId)}/chapters/${chapterNum}?offset=${offset}&limit=30000`);
      const page = result.content || "";
      const content = append ? this.data.content + page : page;
      this.setData({
        content,
        draftContent: append ? this.data.draftContent : (result.draft || content),
        editing: append ? this.data.editing : false,
        contentOffset: offset + page.length,
        totalContent: Number(result.total || content.length),
        hasMoreContent: !!result.has_more,
        chapter: chapterNum,
        canNext: this.data.totalChapters ? chapterNum < this.data.totalChapters : true
      });
    } catch (error) {
      this.setData({ content: "", error: error.message || "这一章还没有正文" });
    } finally {
      this.setData({ readingLoading: false });
    }
  },

  retryCurrent() {
    if (this.data.novelId) this.loadNovel();
  },

  retryChapter() {
    this.loadChapter(this.data.chapter);
  },

  loadMoreContent() {
    if (!this.data.hasMoreContent || this.data.readingLoading) return;
    this.loadChapter(this.data.chapter, true);
  },

  openDraftEditor() {
    this.setData({ editing: true, draftContent: this.data.draftContent || this.data.content });
  },

  closeDraftEditor() {
    this.setData({ editing: false });
  },

  onDraftInput(event) {
    this.setData({ draftContent: event.detail.value });
  },

  async saveDraft() {
    if (this.data.draftSaving || !this.data.novelId) return;
    this.setData({ draftSaving: true, error: "" });
    try {
      await api.post(`/v1/novels/${encodeURIComponent(this.data.novelId)}/chapters/${this.data.chapter}/draft`, {
        content: this.data.draftContent || ""
      });
      this.setData({ editing: false });
      wx.showToast({ title: "草稿已保存", icon: "success" });
    } catch (error) {
      this.setData({ error: error.message || "草稿保存失败" });
    } finally {
      this.setData({ draftSaving: false });
    }
  },

  previousChapter() {
    if (this.data.chapter > 1) this.loadChapter(this.data.chapter - 1);
  },

  nextChapter() {
    if (this.data.canNext) this.loadChapter(this.data.chapter + 1);
  },

  openShelf() {
    wx.switchTab({ url: "/pages/shelf/index" });
  },

  openCreate() {
    wx.switchTab({ url: "/pages/create/index" });
  },

  async startGeneration() {
    if (!this.data.novelId || this.data.jobStatus === "queued" || this.data.jobStatus === "running") return;
    const keyStorage = `generation-key:${this.data.novelId}:${this.data.chapter}`;
    let idempotencyKey = wx.getStorageSync(keyStorage);
    if (!idempotencyKey) {
      idempotencyKey = `mini-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
      wx.setStorageSync(keyStorage, idempotencyKey);
    }
    this.setData({ jobStatus: "queued", jobProgress: 0, jobPhase: "排队中", jobStatusText: this.statusText("queued"), error: "" });
    try {
      const job = await api.post(
        `/v1/novels/${encodeURIComponent(this.data.novelId)}/chapter-jobs`,
        { chapter_num: this.data.chapter, writing_mode: "webnovel" },
        { header: { "Idempotency-Key": idempotencyKey } }
      );
      wx.setStorageSync("activeJobId", job.job_id);
      this.setData({
        jobId: job.job_id,
        jobStatus: job.status,
        jobProgress: job.progress || 0,
        jobPhase: job.phase || "排队中",
        jobStatusText: this.statusText(job.status)
      });
      this.pollJob();
    } catch (error) {
      if (error.statusCode >= 400 && error.statusCode < 500 && error.statusCode !== 429) {
        wx.removeStorageSync(keyStorage);
      }
      this.setData({
        jobStatus: "",
        jobStatusText: "",
        error: error.statusCode === 0 ? "网络暂时中断，保留任务请求，稍后可继续重试。" : (error.message || "生成任务创建失败")
      });
    }
  },

  async cancelGeneration() {
    if (!this.data.jobId || !["queued", "running"].includes(this.data.jobStatus)) return;
    try {
      const job = await api.post(`/v1/jobs/${this.data.jobId}/cancel`);
      wx.removeStorageSync("activeJobId");
      this.setData({ jobStatus: job.status, jobPhase: job.phase || "已取消", jobStatusText: this.statusText(job.status) });
    } catch (error) {
      this.setData({ error: error.message || "取消失败，请稍后重试" });
    }
  },

  async pollJob() {
    if (!this.data.jobId || this._polling) return;
    this._polling = true;
    try {
      const job = await api.get(`/v1/jobs/${this.data.jobId}`);
      this.setData({
        jobStatus: job.status || "",
        jobProgress: job.progress || 0,
        jobPhase: job.phase || "",
        jobStatusText: this.statusText(job.status)
      });
      if (job.status === "succeeded") {
        wx.removeStorageSync("activeJobId");
        await this.loadChapter(job.result && job.result.chapter_num || this.data.chapter);
        this.setData({ error: "" });
        return;
      }
      if (["failed", "cancelled"].includes(job.status)) {
        wx.removeStorageSync("activeJobId");
        wx.removeStorageSync(`generation-key:${this.data.novelId}:${this.data.chapter}`);
        this.setData({ error: job.error_message || "生成未完成，可重新发起任务" });
        return;
      }
      this._pollTimer = setTimeout(() => this.pollJob(), 2000);
    } catch (error) {
      // 离开页面或弱网时保留 job_id，下一次 onShow 会继续查询。
      this.setData({ error: error.message || "任务状态暂时无法读取" });
      this._pollTimer = setTimeout(() => this.pollJob(), 4000);
    } finally {
      this._polling = false;
    }
  },

  statusText(status) {
    return {
      queued: "排队中",
      running: "生成中",
      succeeded: "已完成",
      failed: "生成失败",
      cancel_requested: "正在取消",
      cancelled: "已取消"
    }[status] || "等待处理";
  }
});
