import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import { createApp } from 'vue'
import App from './App.vue'
import './styles/tokens.css'
import './styles/base.css'
import './styles/element-theme.css'
import './styles/motion.css'

createApp(App).use(ElementPlus, { locale: zhCn }).mount('#app')
