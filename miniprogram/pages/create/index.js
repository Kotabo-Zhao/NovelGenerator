const api = require("../../utils/api");

Page({
  data: {
    step: 0,
    steps: [
      { index: 0, label: "01 灵感" },
      { index: 1, label: "02 基调" },
      { index: 2, label: "03 确认" }
    ],
    examples: ["一个秘密", "一次失踪", "一件不可能的物品"],
    wordPresets: [
      { value: "30000", label: "短篇 3 万" },
      { value: "100000", label: "中篇 10 万" },
      { value: "500000", label: "长篇 50 万" }
    ],
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

  useExample(event) {
    const example = event.currentTarget.dataset.example;
    const suffix = {
      "一个秘密": "一个人发现，自己一直守护的秘密其实来自未来的自己。",
      "一次失踪": "一座小城里，每隔十年就会有一个人从所有人的记忆中消失。",
      "一件不可能的物品": "旧货市场里有一台只会播放明天新闻的收音机。"
    }[example] || "";
    this.setData({ inspiration: suffix, error: "" });
  },

  input(event) {
    this.setData({ [event.currentTarget.dataset.field]: event.detail.value });
  },

  nextStep() {
    if (this.data.step === 0 && this.data.inspiration.trim().length < 6) {
      this.setData({ error: "先写至少 6 个字的灵感，人物、冲突或一个画面都可以。" });
      return;
    }
    if (this.data.step === 1) {
      const targetWords = Number(this.data.targetWords);
      if (!Number.isInteger(targetWords) || targetWords < 1000 || targetWords > 2000000) {
        this.setData({ error: "目标篇幅需填写 1000～2000000 之间的整数。" });
        return;
      }
    }
    this.setData({ step: Math.min(2, this.data.step + 1), error: "" });
  },

  goStep(event) {
    this.setData({ step: Number(event.currentTarget.dataset.step), error: "" });
  },

  previousStep() {
    this.setData({ step: Math.max(0, this.data.step - 1), error: "" });
  },

  async submit() {
    if (this.data.submitting) return;
    const targetWords = Number(this.data.targetWords);
    if (!Number.isInteger(targetWords) || targetWords < 1000 || targetWords > 2000000) {
      this.setData({ error: "目标篇幅需填写 1000～2000000 之间的整数。" });
      return;
    }
    this.setData({ submitting: true, error: "" });
    const createKeyStorage = "novel-create-idempotency-key";
    let idempotencyKey = wx.getStorageSync(createKeyStorage);
    if (!idempotencyKey) {
      idempotencyKey = `mini-create-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
      wx.setStorageSync(createKeyStorage, idempotencyKey);
    }
    try {
      const result = await api.post("/v1/novels", {
        title: this.data.title.trim(),
        genre: this.data.genre,
        style: this.data.style,
        inspiration: this.data.inspiration.trim(),
        target_words: targetWords,
        natural_names: true,
        normal_pacing: false,
        fast_food: false,
        creative_enhance: false
      }, {
        timeout: 60000,
        header: { "Idempotency-Key": idempotencyKey }
      });
      const novel = result.novel || {};
      const novelId = result.novel_id || novel.id || novel.novel_id || novel.title || this.data.title;
      wx.setStorageSync("activeNovelId", novelId);
      getApp().globalData.activeNovelId = novelId;
      wx.removeStorageSync(createKeyStorage);
      wx.showToast({ title: "作品已创建", icon: "success" });
      setTimeout(() => wx.switchTab({ url: "/pages/write/index" }), 450);
    } catch (error) {
      if (error.statusCode >= 400 && error.statusCode < 500 && error.statusCode !== 429) {
        wx.removeStorageSync(createKeyStorage);
      }
      this.setData({ error: error.statusCode === 0 ? "网络暂时中断，保留本次请求，点击生成大纲可继续重试。" : (error.message || "创建失败，请稍后重试") });
    } finally {
      this.setData({ submitting: false });
    }
  }
});
