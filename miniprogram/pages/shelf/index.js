const api = require("../../utils/api");

Page({
  data: {
    novels: [],
    loading: false,
    error: "",
    loggedIn: false,
    skeletons: [1, 2]
  },

  onShow() {
    const app = getApp();
    this.setData({ loggedIn: !!(app.globalData && app.globalData.accessToken) });
    this.loadNovels();
  },

  async loadNovels() {
    this.setData({ loading: true, error: "" });
    try {
      const result = await api.get("/v1/novels");
      const novels = (result.novels || []).map(novel => {
        const totalChapters = Number(novel.state && novel.state.total_chapters || 0);
        const completed = Number(novel.disk_chapters || 0);
        return {
          ...novel,
          totalChapters,
          progressPercent: totalChapters ? Math.min(100, Math.round(completed / totalChapters * 100)) : 0,
          coverLetter: (novel.title || novel.id || "卷").slice(0, 1)
        };
      });
      this.setData({ novels });
    } catch (error) {
      this.setData({ error: error.message || "暂时无法加载书架" });
    } finally {
      this.setData({ loading: false });
    }
  },

  goCreate() {
    wx.switchTab({ url: "/pages/create/index" });
  },

  goProfile() {
    wx.switchTab({ url: "/pages/profile/index" });
  },

  openNovel(event) {
    const novelId = event.currentTarget.dataset.id;
    wx.setStorageSync("activeNovelId", novelId);
    const app = getApp();
    app.globalData.activeNovelId = novelId;
    wx.switchTab({ url: "/pages/write/index" });
  }
});
