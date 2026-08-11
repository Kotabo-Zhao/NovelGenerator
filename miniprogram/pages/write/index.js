const api = require("../../utils/api");

Page({
  data: {
    novelId: "",
    novel: null,
    chapter: 1,
    content: "",
    loading: false,
    readingLoading: false,
    jobId: "",
    jobStatus: "",
    jobProgress: 0,
    jobPhase: "",
    error: ""
  },

  onShow() {
    const novelId = wx.getStorageSync("activeNovelId") || getApp().globalData.activeNovelId || "";
    const jobId = wx.getStorageSync("activeJobId") || "";
    if (novelId && novelId !== this.data.novelId) {
      this.setData({ novelId, novel: null, content: "", error: "", jobId });
      this.loadNovel();
      return;
    }
    if (jobId) {
      if (jobId !== this.data.jobId) this.setData({ jobId });
      this.pollJob();
    }
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
      const current = novel.state && novel.state.current_chapter || 0;
      this.setData({ novel, chapter: Math.max(1, current || 1) });
      if (current > 0) this.loadChapter(current);
      if (this.data.jobId) this.pollJob();
    } catch (error) {
      this.setData({ error: error.message || "无法读取作品" });
    } finally {
      this.setData({ loading: false });
    }
  },

  async loadChapter(chapter) {
    if (!this.data.novelId) return;
    this.setData({ readingLoading: true, error: "" });
    try {
      const result = await api.get(`/v1/novels/${encodeURIComponent(this.data.novelId)}/chapters/${chapter}`);
      this.setData({ content: result.content || "", chapter });
    } catch (error) {
      this.setData({ content: "", error: error.message || "这一章还没有正文" });
    } finally {
      this.setData({ readingLoading: false });
    }
  },

  previousChapter() {
    if (this.data.chapter > 1) this.loadChapter(this.data.chapter - 1);
  },

  nextChapter() {
    this.loadChapter(this.data.chapter + 1);
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
    this.setData({ jobStatus: "queued", jobProgress: 0, jobPhase: "排队中", error: "" });
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
        jobPhase: job.phase || "排队中"
      });
      this.pollJob();
    } catch (error) {
      wx.removeStorageSync(keyStorage);
      this.setData({ jobStatus: "", error: error.message || "生成任务创建失败" });
    }
  },

  async cancelGeneration() {
    if (!this.data.jobId || !["queued", "running"].includes(this.data.jobStatus)) return;
    try {
      const job = await api.post(`/v1/jobs/${this.data.jobId}/cancel`);
      this.setData({ jobStatus: job.status, jobPhase: job.phase || "已取消" });
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
        jobPhase: job.phase || ""
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
  }
});
