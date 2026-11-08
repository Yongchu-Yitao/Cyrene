export const researchSources = [
 {id:'mcp-ui',title:'MCP Apps：工具与界面的双向通信',publisher:'OpenAI Developers',kind:'官方文档',url:'https://developers.openai.com/plugins/build/chatgpt-ui',summary:'UI 接收结构化工具结果，通过消息桥调用工具，并把选中项等上下文传给模型。',finding:'把模型决策、工具执行和界面呈现分开，让界面使用服务端的权威状态。',tags:['交互协议','状态同步'],selected:true},
 {id:'extensions',title:'Plugin Extensions：持续存在的工作空间',publisher:'OpenAI Developers',kind:'官方文档',url:'https://developers.openai.com/plugins/build/extensions',summary:'对话侧面板、文件查看器和表单让用户在聊天之外继续处理任务。',finding:'复杂任务可以拥有持续存在的工作区，输入框与工作区共同维护任务上下文。',tags:['工作区','上下文'],selected:true},
 {id:'chatkit',title:'ChatKit：交互组件与 Actions',publisher:'OpenAI Developers',kind:'官方文档',url:'https://developers.openai.com/api/docs/guides/custom-chatkit',summary:'组件可以触发结构化动作；服务端处理后流式返回新的消息或组件更新。',finding:'明确的按钮操作可以直接调用业务逻辑；需要理解和规划的动作再交给 Agent。',tags:['组件','动作'],selected:true},
 {id:'guidelines',title:'对话式应用的 UI 设计原则',publisher:'OpenAI Developers',kind:'设计指南',url:'https://developers.openai.com/plugins/concepts/ui-guidelines',summary:'根据任务复杂度选择内联、全屏或画中画呈现，保持对话入口可用。',finding:'用最小够用的界面完成当前任务；把复杂编辑放进工作区，避免信息重复。',tags:['设计','呈现'],selected:false},
];
export const destinations = {
 hangzhou:{name:'杭州',aliases:['杭州','hangzhou'],country:'中国',currency:'CNY',center:[30.249,120.145],subtitle:'把时间留给湖边、茶园和老街。',transport:'市区以步行与公共交通衔接；跨区域地点建议分天安排。',stay:'可优先比较湖滨、武林广场周边住宿，具体价格需另行查询。',food:'可以把杭帮菜、茶饮和小吃留在沿途；餐饮预算为演示估算。',official:'https://wgly.hangzhou.gov.cn/',places:[
 ['broken-bridge','断桥残雪',30.2588,120.1482,'湖畔漫步','沿白堤步行，感受湖面的开阔。',1,90,0,'09:00'],
 ['quyuan','曲院风荷',30.2494,120.1315,'公园','放慢节奏，沿湖散步。',1,90,0,'11:00'],
 ['hefang','河坊街',30.2324,120.1705,'街区','留一段时间逛老街，安排晚餐。',1,120,0,'16:00'],
 ['lingyin','灵隐寺周边',30.2405,120.1007,'文化','山林与古建，建议出发前核实预约要求。',2,150,100,'09:30'],
 ['longjing','龙井村',30.2181,120.1014,'茶园','看茶田，找一处地方休息。',2,120,0,'14:00'],
 ['xixi','西溪湿地',30.2695,120.0615,'自然','为湿地步道留出较完整的半天。',3,180,100,'09:30'],
 ]},
 shanghai:{name:'上海',aliases:['上海','shanghai'],country:'中国',currency:'CNY',center:[31.229,121.477],subtitle:'从街区散步，到江岸的城市天际线。',transport:'地铁串联大多数市区目的地，街区内适合步行。',stay:'比较人民广场、静安或徐汇附近住宿，并核实到站步行距离。',food:'安排一顿本帮菜，沿途留出咖啡和小吃时间。',official:'https://www.meet-in-shanghai.net/',places:[
 ['bund','外滩',31.2402,121.4904,'江岸','沿黄浦江步行，观察两岸建筑。',1,90,0,'09:30'],
 ['yuyuan','豫园周边',31.2272,121.4921,'文化','老城街区与园林周边漫步。',1,120,50,'13:00'],
 ['nanjing','南京东路',31.2352,121.4782,'街区','城市步行街，可结合晚餐。',1,90,0,'17:00'],
 ['wukang','武康大楼',31.2030,121.4352,'街区','沿武康路与附近街区散步。',2,90,0,'10:00'],
 ['fuxing','复兴公园',31.2146,121.4670,'公园','安排轻松的午后停留。',2,90,0,'14:00'],
 ['l ujiazui','陆家嘴滨江',31.2355,121.4998,'江岸','从另一侧观看浦西天际线。',3,120,0,'16:00'],
 ]},
 chengdu:{name:'成都',aliases:['成都','chengdu'],country:'中国',currency:'CNY',center:[30.661,104.063],subtitle:'公园、茶馆，还有不赶时间的街巷。',transport:'以地铁和短途步行为主，熊猫基地建议单独安排半天。',stay:'比较天府广场、春熙路周边住宿，关注实际通勤距离。',food:'火锅、川菜与小吃可按个人口味选择；预算不代表商家报价。',official:'https://www.gochengdu.cn/',places:[
 ['peoples','人民公园',30.6586,104.0551,'公园','留时间喝茶、观察城市生活。',1,120,0,'09:30'],
 ['kuanzhai','宽窄巷子',30.6683,104.0533,'街区','慢慢逛街巷与庭院。',1,90,0,'13:00'],
 ['taikoo','太古里',30.6527,104.0828,'街区','现代街区与晚餐安排。',1,120,0,'17:00'],
 ['panda','成都大熊猫繁育研究基地',30.7403,104.1444,'自然','出发前核实开放与预约规则。',2,180,100,'08:30'],
 ['wuhou','武侯祠周边',30.6457,104.0494,'文化','了解历史，再去附近街区走走。',2,120,80,'14:00'],
 ['dufu','杜甫草堂',30.6601,104.0280,'文化','为园林与展陈留足时间。',3,150,80,'10:00'],
 ]},
 kyoto:{name:'京都',aliases:['京都','kyoto'],country:'日本',currency:'CNY',center:[35.004,135.773],subtitle:'沿着山麓与河岸，走进古都的日常。',transport:'公交、铁路与步行结合；岚山与东山建议分天安排。',stay:'可比较京都站、四条河原町周边住宿。所有预算统一以人民币估算。',food:'为定食、茶点和沿途休息留出预算；未查询实际餐厅价格。',official:'https://kyoto.travel/en/',places:[
 ['kiyomizu','清水寺',34.9949,135.7850,'文化','山坡上的寺院与城市景观。',1,120,60,'09:00'],
 ['ninenzaka','二年坂',34.9980,135.7807,'街区','坡道与传统街屋，适合步行。',1,90,0,'12:00'],
 ['gion','祇园白川',35.0066,135.7744,'街区','河岸与街巷散步，尊重当地拍摄规则。',1,90,0,'16:00'],
 ['fushimi','伏见稻荷大社',34.9671,135.7727,'文化','按体力选择参道步行距离。',2,150,0,'09:00'],
 ['nishiki','锦市场',35.0050,135.7649,'美食','探索市场与周边街区。',2,90,0,'14:00'],
 ['arashiyama','岚山渡月桥',35.0094,135.6770,'自然','河岸、山景与周边散步。',3,180,0,'10:00'],
 ]},
};
for(const [key,city] of Object.entries(destinations))city.places=city.places.map(([id,name,lat,lng,category,description,day,minutes,cost,time])=>({id:id.replaceAll(' ',''),name,lat,lng,category,description,day,minutes,cost,time,included:true}));
export const samplePrompts={research:'调研 AI 个人助理的交互工作台应该怎么设计，比较实现方案。',travel:'我想去杭州玩 3 天，预算 2000 元，节奏轻松一点。'};
