Page({
  data: {
    chatHistory: [], // 对话记录
    inputText: '',
    isThinking: false,
    scrollToId: '',
    showReport: false, // 是否展示最终报告
    reportData: {} // 报告数据
  },

  onInput(e) { this.setData({ inputText: e.detail.value }) },

  // 点击发送
  sendMsg() {
    if (!this.data.inputText.trim()) return;

    const userText = this.data.inputText;
    const history = this.data.chatHistory;
    history.push({ role: 'user', content: userText });

    this.setData({
      chatHistory: history,
      inputText: '',
      isThinking: true
    });
    this.scrollToBottom();

    // 模拟后端请求（这里写死逻辑）
    setTimeout(() => {
      this.mockBackendLogic(userText);
    }, 1000);
  },

  // 模拟后端逻辑（方便你测试各种情况）
  mockBackendLogic(text) {
    const history = this.data.chatHistory;

    // 测试1：急症拦截
    if (text.includes('胸痛') || text.includes('喘不上气')) {
      wx.navigateTo({ url: '/pages/emergency/emergency' });
      this.setData({ isThinking: false });
      return;
    }

    // 测试2：多轮追问（如果用户只说了“肚子疼”）
    if (text === '肚子疼') {
      history.push({ role: 'ai', content: '请问是上腹部还是下腹部？是绞痛还是隐痛？' });
      this.setData({ chatHistory: history, isThinking: false });
      this.scrollToBottom();
      return;
    }

    // 测试3：给出最终报告（只要用户回答了，就出报告）
    history.push({ role: 'ai', content: '信息已收集完毕，正在为您生成分诊报告...' });
    this.setData({
      chatHistory: history,
      isThinking: false,
      showReport: true,
      reportData: {
        department: '消化内科',
        backup: '全科医学科',
        reason: '您描述的“' + text + '”伴有反酸，建议消化内科就诊。',
        script: '您好，我腹痛并伴有反酸，想挂消化内科。'
      }
    });
    this.scrollToBottom();
  },

  // 滚动到底部
  scrollToBottom() {
    const len = this.data.chatHistory.length;
    this.setData({ scrollToId: `msg-${len - 1}` });
  },

  // 复制话术
  copyScript() {
    wx.setClipboardData({
      data: this.data.reportData.script,
      success: () => wx.showToast({ title: '已复制', icon: 'success' })
    });
  },

  // 导航
  openMap() {
    wx.showToast({ title: '正在调起地图...', icon: 'none' });
  }
})