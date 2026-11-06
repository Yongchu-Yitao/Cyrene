export const initialTasks = [
  { id: 'hamster', title: '金丝熊饲养调研', description: '整理可靠的饲养资料，生成一份实用指南。', status: 'blocked', time: '19:46', icon: 'world', steps: ['搜集并筛选参考资料', '整理环境、饮食与日常照护', '生成参考页面与资料清单'], completed: 2 },
  { id: 'weekly', title: '整理本周工作', description: '汇总本周进展，准备晚间复盘。', status: 'review', time: '18:20', icon: 'file-text', steps: ['收集本周工作记录', '整理已完成事项', '确认下周优先级'], completed: 2 },
  { id: 'weekend', title: '周末出行计划', description: '为周末留出一段轻松的城市漫步。', status: 'done', time: '17:40', icon: 'calendar', steps: ['整理出行偏好', '安排路线与时间', '生成出行清单'], completed: 3 },
];
export const initialEvents = [{ id: 1, time: '20:30', title: '阅读与整理', note: '整理金丝熊饲养资料，补充笔记', done: false }, { id: 2, time: '21:00', title: '本周复盘', note: '回顾进展，选出下周的三件重要事情', done: false }];
export const initialMemories = [{id:1, title:'沟通偏好', text:'用中文交流，先给结论，再补充必要的细节。', enabled:true}, {id:2, title:'工作习惯', text:'在周五晚上整理本周进展，规划下周重点。', enabled:true}, {id:3, title:'出行偏好', text:'喜欢安静的街区、步行路线和不太紧凑的安排。', enabled:true}];
export const documents = [
  {id:'guide', title:'饲养指南.md', kind:'研究笔记', time:'19:46', color:'peach', summary:'从居住环境到日常照护，把需要准备的事情整理在一起。', content:'# 金丝熊饲养指南\n\n这是一份界面演示文档，内容用于预览排版。\n\n## 01 · 开始之前\n先了解饲养所需的空间、时间和长期照护责任，再准备合适的生活环境。\n\n## 02 · 准备清单\n- 合适的居住空间与躲避处\n- 安全的垫料、饮水和食物\n- 适合活动与探索的设施\n- 日常清洁与观察记录\n\n## 03 · 待进一步核实\n具体的环境参数、饮食与健康问题，应参考可靠的专业资料。本演示未进行实时检索。\n\n## 下一步\n核实资料来源，补充具体准备清单。'},
  {id:'weekly',title:'本周工作总结.md',kind:'工作文档',time:'18:20',color:'blue',summary:'已完成的进展、值得记录的收获，以及下周的优先事项。',content:'# 本周工作总结\n\n11月2日 — 11月6日 · 演示数据\n\n## 本周完成\n- 完成个人助理的第一轮界面梳理\n- 整理浏览器与文件分屏使用流程\n- 完成工具验证与问题记录\n\n## 正在推进\n- 助理首页与任务状态设计\n- 更清晰的成果预览与任务恢复体验\n\n## 下周重点\n1. 确认首页布局\n2. 验证核心交互\n3. 整理用户反馈\n\n## 复盘笔记\n把最重要的事情留在视线里，让每一步都有明确的结果。'},
  {id:'trip',title:'周末出行计划.md',kind:'生活计划',time:'17:40',color:'green',summary:'一条松弛的步行路线，一份留有余地的周末安排。',content:'# 周末出行计划\n\n一段不赶时间的城市漫步 · 演示数据\n\n## 上午\n10:00 出发，在附近的咖啡馆吃早餐。\n\n## 下午\n14:00 去公园散步，带上一本书。\n\n## 傍晚\n17:30 在回家的路上吃一顿简单的晚餐。\n\n## 随身清单\n- 水杯\n- 耳机与充电设备\n- 舒适的鞋\n\n出发前确认天气与场所开放时间。'},
  {id:'split',title:'split-check.md',kind:'验证记录',time:'17:58',color:'blue',summary:'浏览器与文件分屏的验证记录。',content:'# 分屏验证记录\n\n## 浏览器\n浏览器区域支持与对话并排查看。\n\n## 文件\n文件预览保持当前任务的上下文。\n\n## 验证结论\n这份文档用于演示侧栏文件入口与预览交互。'}
];
export const statusLabels = { blocked:'等待连接', review:'待你确认', running:'正在进行', paused:'已暂停', done:'已完成' };
