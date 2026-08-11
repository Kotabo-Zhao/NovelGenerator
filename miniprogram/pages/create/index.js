const api = require("../../utils/api");

Page({
  data: {
    step: 0,
    genres: ["玄幻", "都市", "悬疑", "科幻", "言情", "历史"],
    styles: ["热血爽文", "轻松日常", "悬疑克制", "细腻情感"],
    genre: "玄幻",
    style: "热血爽文",
    title: "",
    inspiration: "",
    targetWords: "500000",
    submitting: false,
    error: ""
  },

  choose(event) {
    const field = event.currentTarget.dataset.field;
    this.setData({ [field]: event.currentTarget.dataset.value });
  },

  input(event) {
    this.setData({ [event.currentTarget.dataset.field]: event.detail.value });
  },

  nextStep() {
    if (this.data.step === 0 && !this.data.inspiration.trim()) {
      this.setData({ error: "先写下一句灵感，哪怕只有一个画面。" });
      return;
    }
    this.setData({ step: Math.min(2, this.data.step + 1), error: "" });
  },

  previousStep() {
    this.setData({ step: Math.max(0, this.data.step - 1), error: "" });
  },

  async submit() {
    if (this.data.submitting) return;
    this.setData({ submitting: true, error: "" });
    try {
      const result = await api.post("/api/novels", {
        title: this.data.title.trim(),
        genre: this.data.genre,
        style: this.data.style,
        inspiration: this.data.inspiration.trim(),
        target_words: Number(this.data.targetWords) || 500000,
        natural_names: true,
        normal_pacing: false,
        fast_food: false,
        creative_enhance: false
      }, { timeout: 60000 });
      const novel = result.novel || {};
      const novelId = novel.id || novel.novel_id || novel.title || this.data.title;
      wx.setStorageSync("activeNovelId", novelId);
      getApp().globalData.activeNovelId = novelId;
      wx.showToast({ title: "作品已创建", icon: "success" });
      setTimeout(() => wx.switchTab({ url: "/pages/write/index" }), 450);
    } catch (error) {
      this.setData({ error: error.message || "创建失败，请稍后重试" });
    } finally {
      this.setData({ submitting: false });
    }
  }
});
