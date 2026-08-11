Page({
  data: {
    items: [
      { key: "privacy", title: "隐私与数据", desc: "登录、作品删除和导出将在认证接入后开放" },
      { key: "voice", title: "朗读设置", desc: "统一管理语音，不在写作页堆叠开关" },
      { key: "help", title: "帮助与反馈", desc: "告诉我们哪里需要变得更好" }
    ]
  },

  onTap(event) {
    const item = this.data.items.find(entry => entry.key === event.currentTarget.dataset.key);
    if (item) wx.showToast({ title: item.title + "即将开放", icon: "none" });
  }
});
