import { SecretStorageStatus } from './types';

export function SecretStorageNotice({ storage }: { storage: SecretStorageStatus | null }) {
  if (!storage) return <p className="small muted" role="status">密钥文件权限尚未检查。</p>;
  if (storage.status === 'missing') return <p className="small muted">未保存本机密钥文件。</p>;
  if (storage.status === 'owner_only') return <p className="small muted">密钥文件权限位为 0600，仅所有者可读写；仍为明文存储，未检查 ACL。</p>;
  return <section className="agent-warning" role="alert" aria-label="密钥文件权限提醒">
    <strong>请检查本机密钥文件{storage.mode ? `（当前权限 ${storage.mode}）` : ''}</strong>
    <ul>{storage.warnings.map(warning => <li key={warning.code}>{warning.message}</li>)}</ul>
    <p className="small">位置：服务数据库同目录的 secrets.json。检查不会读取展示密钥，也不会自动修改权限。
      确认文件归属后，可由你将普通文件权限恢复为 0600；若可能已泄露，请更换密钥。此处每 5 秒重新检查，不调用模型。</p>
  </section>;
}
