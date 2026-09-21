import { Layout, Typography } from 'antd'
import AnalysisPage from './pages/AnalysisPage'

const { Header, Content, Footer } = Layout

export default function App() {
  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header style={{ display: 'flex', alignItems: 'center' }}>
        <Typography.Title level={4} style={{ color: '#fff', margin: 0 }}>
          OpenMap · 15 分钟生活圈智能体检助手
        </Typography.Title>
      </Header>
      <Content style={{ padding: 16 }}>
        <AnalysisPage />
      </Content>
      <Footer style={{ textAlign: 'center', color: '#999' }}>
        M1 骨架：选址与 POI 检索 · 等时圈/体检报告见路线图 M2/M3 · Apache-2.0
      </Footer>
    </Layout>
  )
}
