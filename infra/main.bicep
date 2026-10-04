// Orders Voice Analyst â€“ Azure infrastructure.
// Every secret is written to Key Vault, and Container Apps read them through Key Vault
// references using a user-assigned managed identity. All ingress is HTTPS only.
targetScope = 'resourceGroup'

param location string = resourceGroup().location
param prefix string = 'ordersva'

@description('Foundry project endpoint, e.g. https://<res>.services.ai.azure.com/api/projects/<project>')
param foundryProjectEndpoint string
param foundryModelDeployment string = 'gpt-4.1'

@description('Image tag pushed to the ACR created here (see README).')
param imageTag string = 'latest'
param allowedOrigins string = ''

@description('Set false on the first deployment (before images are pushed to ACR).')
param deployApps bool = true

// Databricks (workspace is created separately; CMK/encryption configured there)
param databricksHost string          // adb-xxxx.azuredatabricks.net
param databricksHttpPath string      // /sql/1.0/warehouses/xxxx
@secure()
param databricksToken string

// Snowflake
param snowflakeAccount string        // orgname-accountname
param snowflakeUser string = 'MCP_ORDERS_READER'
@secure()
param snowflakePrivateKey string     // PEM

// MySQL
param mysqlAdminLogin string = 'ordersadmin'
@secure()
param mysqlAdminPassword string
@secure()
param mysqlReaderPassword string
@secure()
param mysqlLoaderPassword string

@secure()
param mcpApiKey string = newGuid()

var suffix = uniqueString(resourceGroup().id)
var kvSecretsUser = '4633458b-17de-408a-b874-0445c86b69e6'
var acrPull = '7f951dda-4ed3-4680-a7ca-43fe172d538f'
var blobContributor = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'

// ---------- Identity ----------
resource uai 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-id'
  location: location
}

// ---------- Key Vault ----------
resource kv 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: '${prefix}-kv-${take(suffix, 6)}'
  location: location
  properties: {
    tenantId: subscription().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enableSoftDelete: true
    enablePurgeProtection: true
    softDeleteRetentionInDays: 90
  }
}

resource kvRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: kv
  name: guid(kv.id, uai.id, kvSecretsUser)
  properties: {
    principalId: uai.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', kvSecretsUser)
  }
}

// ---------- Azure AI Speech ----------
resource speech 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: '${prefix}-speech-${take(suffix, 6)}'
  location: location
  kind: 'SpeechServices'
  sku: { name: 'S0' }
  properties: { customSubDomainName: '${prefix}-speech-${take(suffix, 6)}' }
}

// ---------- Storage (landing zone for pipelines) ----------
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: '${prefix}st${take(suffix, 8)}'
  location: location
  kind: 'StorageV2'
  sku: { name: 'Standard_ZRS' }
  properties: {
    isHnsEnabled: true
    supportsHttpsTrafficOnly: true
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    encryption: {
      requireInfrastructureEncryption: true   // double encryption at rest
      keySource: 'Microsoft.Storage'
      services: { blob: { enabled: true }, file: { enabled: true } }
    }
  }
}

resource landing 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  name: '${storage.name}/default/landing'
}

resource blobRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storage
  name: guid(storage.id, uai.id, blobContributor)
  properties: {
    principalId: uai.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', blobContributor)
  }
}

// ---------- MySQL Flexible Server (encrypted at rest; TLS enforced) ----------
resource mysql 'Microsoft.DBforMySQL/flexibleServers@2023-12-30' = {
  name: '${prefix}-mysql-${take(suffix, 6)}'
  location: location
  sku: { name: 'Standard_B2ms', tier: 'Burstable' }
  properties: {
    version: '8.0.21'
    administratorLogin: mysqlAdminLogin
    administratorLoginPassword: mysqlAdminPassword
    storage: { storageSizeGB: 32, autoGrow: 'Enabled' }
    backup: { backupRetentionDays: 7, geoRedundantBackup: 'Disabled' }
    // Data, backups and logs are AES-256 encrypted at rest. For CMK, add
    // dataEncryption { type: 'AzureKeyVault', primaryKeyURI: ... } + a UAI.
  }
}

resource mysqlTls 'Microsoft.DBforMySQL/flexibleServers/configurations@2023-12-30' = {
  parent: mysql
  name: 'require_secure_transport'
  properties: { value: 'ON', source: 'user-override' }
}

resource mysqlTlsVersion 'Microsoft.DBforMySQL/flexibleServers/configurations@2023-12-30' = {
  parent: mysql
  name: 'tls_version'
  properties: { value: 'TLSv1.2,TLSv1.3', source: 'user-override' }
  dependsOn: [ mysqlTls ]
}

resource mysqlDb 'Microsoft.DBforMySQL/flexibleServers/databases@2023-12-30' = {
  parent: mysql
  name: 'orders_db'
  properties: { charset: 'utf8mb4', collation: 'utf8mb4_0900_ai_ci' }
}

resource mysqlAllowAzure 'Microsoft.DBforMySQL/flexibleServers/firewallRules@2023-12-30' = {
  parent: mysql
  name: 'AllowAzureServices'   // tighten to private endpoint / VNet integration for production
  properties: { startIpAddress: '0.0.0.0', endIpAddress: '0.0.0.0' }
}

// ---------- Key Vault secrets ----------
var secrets = {
  'mcp-api-key': mcpApiKey
  'databricks-token': databricksToken
  'snowflake-private-key': snowflakePrivateKey
  'mysql-admin-password': mysqlAdminPassword
  'mysql-reader-password': mysqlReaderPassword
  'mysql-loader-password': mysqlLoaderPassword
}

resource kvSecrets 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = [for s in items(secrets): {
  parent: kv
  name: s.key
  properties: { value: s.value }
}]

resource speechKeySecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: kv
  name: 'speech-key'
  properties: { value: speech.listKeys().key1 }
}

func kvRef(vaultUri string, name string, identityId string) object => {
  name: name
  keyVaultUrl: '${vaultUri}secrets/${name}'
  identity: identityId
}

// ---------- Container registry ----------
resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: '${prefix}acr${take(suffix, 8)}'
  location: location
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false }
}

resource acrRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: acr
  name: guid(acr.id, uai.id, acrPull)
  properties: {
    principalId: uai.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPull)
  }
}

// ---------- Container Apps environment ----------
resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${prefix}-logs'
  location: location
  properties: { sku: { name: 'PerGB2018' }, retentionInDays: 30 }
}

resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${prefix}-env'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

var identity = {
  type: 'UserAssigned'
  userAssignedIdentities: { '${uai.id}': {} }
}
var registries = [ { server: acr.properties.loginServer, identity: uai.id } ]
var httpsIngress = {
  external: true
  targetPort: 8080
  transport: 'auto'
  allowInsecure: false            // HTTP is redirected to HTTPS; TLS 1.2+ terminates at ingress
}

// ---------- MCP servers ----------
var mcpServers = [
  {
    name: 'databricks'
    secrets: [ kvRef(kv.properties.vaultUri, 'databricks-token', uai.id) ]
    env: [
      { name: 'DATABRICKS_HOST', value: databricksHost }
      { name: 'DATABRICKS_HTTP_PATH', value: databricksHttpPath }
      { name: 'DATABRICKS_TOKEN', secretRef: 'databricks-token' }
    ]
  }
  {
    name: 'mysql'
    secrets: [ kvRef(kv.properties.vaultUri, 'mysql-reader-password', uai.id) ]
    env: [
      { name: 'MYSQL_HOST', value: mysql.properties.fullyQualifiedDomainName }
      { name: 'MYSQL_USER', value: 'mcp_reader' }
      { name: 'MYSQL_DATABASE', value: 'orders_db' }
      { name: 'MYSQL_PASSWORD', secretRef: 'mysql-reader-password' }
    ]
  }
  {
    name: 'snowflake'
    secrets: [ kvRef(kv.properties.vaultUri, 'snowflake-private-key', uai.id) ]
    env: [
      { name: 'SNOWFLAKE_ACCOUNT', value: snowflakeAccount }
      { name: 'SNOWFLAKE_USER', value: snowflakeUser }
      { name: 'SNOWFLAKE_PRIVATE_KEY', secretRef: 'snowflake-private-key' }
    ]
  }
]

resource mcpApps 'Microsoft.App/containerApps@2024-03-01' = [for (name, i) in ['databricks', 'mysql', 'snowflake']: if (deployApps) {
  name: 'mcp-${name}'
  location: location
  identity: identity
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      ingress: httpsIngress
      registries: registries
      secrets: concat(mcpServers[i].secrets, [ kvRef(kv.properties.vaultUri, 'mcp-api-key', uai.id) ])
    }
    template: {
      containers: [ {
        name: 'mcp'
        image: '${acr.properties.loginServer}/orders-mcp:${imageTag}'
        resources: { cpu: json('0.5'), memory: '1Gi' }
        env: concat(mcpServers[i].env, [
          { name: 'MCP_BACKEND', value: name }
          { name: 'MCP_API_KEY', secretRef: 'mcp-api-key' }
        ])
        probes: [ { type: 'Liveness', httpGet: { path: '/healthz', port: 8080 } } ]
      } ]
      scale: { minReplicas: 1, maxReplicas: 5 }
    }
  }
  dependsOn: [ kvRole, kvSecrets, speechKeySecret, acrRole ]
}]

// ---------- API + web front-end ----------
resource api 'Microsoft.App/containerApps@2024-03-01' = if (deployApps) {
  name: '${prefix}-api'
  location: location
  identity: identity
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      ingress: union(httpsIngress, { targetPort: 8000 })
      registries: registries
      secrets: [
        kvRef(kv.properties.vaultUri, 'mcp-api-key', uai.id)
        kvRef(kv.properties.vaultUri, 'speech-key', uai.id)
      ]
    }
    template: {
      containers: [ {
        name: 'api'
        image: '${acr.properties.loginServer}/orders-api:${imageTag}'
        resources: { cpu: json('1'), memory: '2Gi' }
        env: [
          { name: 'AZURE_CLIENT_ID', value: uai.properties.clientId }   // DefaultAzureCredential -> UAI
          { name: 'FOUNDRY_PROJECT_ENDPOINT', value: foundryProjectEndpoint }
          { name: 'FOUNDRY_MODEL_DEPLOYMENT', value: foundryModelDeployment }
          { name: 'MCP_DATABRICKS_URL', value: 'https://${mcpApps[0]!.properties.configuration.ingress.fqdn}/mcp' }
          { name: 'MCP_MYSQL_URL', value: 'https://${mcpApps[1]!.properties.configuration.ingress.fqdn}/mcp' }
          { name: 'MCP_SNOWFLAKE_URL', value: 'https://${mcpApps[2]!.properties.configuration.ingress.fqdn}/mcp' }
          { name: 'MCP_API_KEY', secretRef: 'mcp-api-key' }
          { name: 'SPEECH_REGION', value: location }
          { name: 'SPEECH_KEY', secretRef: 'speech-key' }
          { name: 'ALLOWED_ORIGINS', value: allowedOrigins }
        ]
        probes: [ { type: 'Liveness', httpGet: { path: '/healthz', port: 8000 } } ]
      } ]
      scale: { minReplicas: 1, maxReplicas: 10 }
    }
  }
  dependsOn: [ kvRole, kvSecrets, speechKeySecret, acrRole ]
}

// ---------- MySQL refresh pipeline (scheduled job) ----------
resource mysqlLoader 'Microsoft.App/jobs@2024-03-01' = if (deployApps) {
  name: '${prefix}-mysql-loader'
  location: location
  identity: identity
  properties: {
    environmentId: env.id
    configuration: {
      triggerType: 'Schedule'
      scheduleTriggerConfig: { cronExpression: '30 * * * *', parallelism: 1, replicaCompletionCount: 1 }
      replicaTimeout: 1800
      replicaRetryLimit: 2
      registries: registries
      secrets: [ kvRef(kv.properties.vaultUri, 'mysql-loader-password', uai.id) ]
    }
    template: {
      containers: [ {
        name: 'loader'
        image: '${acr.properties.loginServer}/orders-mysql-loader:${imageTag}'
        resources: { cpu: json('0.5'), memory: '1Gi' }
        env: [
          { name: 'AZURE_CLIENT_ID', value: uai.properties.clientId }
          { name: 'MYSQL_HOST', value: mysql.properties.fullyQualifiedDomainName }
          { name: 'MYSQL_USER', value: 'orders_loader' }
          { name: 'MYSQL_PASSWORD', secretRef: 'mysql-loader-password' }
          { name: 'STORAGE_ACCOUNT', value: storage.name }
        ]
      } ]
    }
  }
  dependsOn: [ kvRole, kvSecrets, acrRole, blobRole ]
}

output apiUrl string = deployApps ? 'https://${api!.properties.configuration.ingress.fqdn}' : ''
output acrLoginServer string = acr.properties.loginServer
output keyVaultName string = kv.name
output mysqlHost string = mysql.properties.fullyQualifiedDomainName
output storageAccount string = storage.name
output identityPrincipalId string = uai.properties.principalId
