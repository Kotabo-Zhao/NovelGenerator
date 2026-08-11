const api = require("../../utils/api");

Page({
  data: {
    novelId: "",
    novel: null,
    chapter: 1,
    content: "",
    loading: false,
    readingLoading: false,
    error: ""
  },

  onShow() {
    const novelId = wx.getStorageSync("activeNovelId") || getApp().globalData.activeNovelId || "";
    if (novelId && novelId !== this.data.novelId) {
      this.setData({ novelId, novel: null, content: "", error: "" });
      this.loadNovel();
    }
  },

  async loadNovel() {
    this.setData({ loading: true, error: "" });
    try {
      const result = await api.get(`/api/novels/${encodeURIComponent(this.data.novelId)}`);
      const novel = result.novel || {};
      const current = novel.state && novel.state.current_chapter || 0;
      this.setData({ novel, chapter: Math.max(1, current || 1) });
      if (current > 0) this.loadChapter(current);
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
      const result = await api.get(`/api/novels/${encodeURIComponent(this.data.novelId)}/chapters/${chapter}`);
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

  startGeneration() {
    wx.showModal({
      title: "生成任务即将接入",
      content: "当前版本先完成小程序 UI 与数据边界。可恢复的生成任务将在后端 job 接口完成后开放。",
      showCancel: false
    });
  }
});
