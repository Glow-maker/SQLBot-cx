# 背景
由于当前SQLBOT在开启对话的阶段需要绑定数据源，作为统一产品的视角希望将内部产品的统一表达，希望跳过数据源选择的环境。

本文讨论如果自动选择数据源，同时引导用户选择合适的数据源类型。引导用户识别不具备权限的表并进行自动化申请流程。

# 问数流程
![画板](https://cdn.nlark.com/yuque/0/2026/jpeg/365146/1774588525654-ea6e114d-171f-494d-9e24-c8cda0027413.jpeg)



# 实现逻辑
## 如何嵌入外部数据源
1. 在大模型进行question请求处理的过程中。

<!-- 这是一张图片，ocr 内容为： -->
![](https://cdn.nlark.com/yuque/0/2026/png/365146/1774584196899-5580b3d5-0ebd-4366-acd2-055f4aa484ba.png)在未指定数据源的情况下，通过get_assistant_ds方法从assistant中获取数据源，

而Assistant分为3类：

type=0为基础小助手，

type=1为高级小助手。

type=4为网页内嵌。

<!-- 这是一张图片，ocr 内容为： -->
![](https://cdn.nlark.com/yuque/0/2026/png/365146/1774583376347-8c0ef81c-07da-4ccc-bc13-f1aaa9a522b1.png)

当type=2，高级小助手支持通过API获取外部接口实现因此我们只需在sys_assistant中添加config的相关配置。然后从远程返回datasouce接口即可

<!-- 这是一张图片，ocr 内容为： -->
![](https://cdn.nlark.com/yuque/0/2026/png/365146/1774583824981-a8d72ed8-cc4c-467c-8bde-6cf082f49a3a.png)

API的返回格式参考：

```json
{
  "code": 0,  // 或 200
  "data": [
    {
      "id": 1,
      "name": "数据源名称",
      "type": "mysql",  // 数据源类型
      "host": "localhost",
      "port": 3306,
      "user": "username",
      "password": "password",
      "dataBase": "database_name",
      "db_schema": "schema_name",  // 或 schema
      "description": "数据源描述",
      "comment": "数据源注释",
      "extraParams": "额外参数",
      "tables": [
        {
          "id": 1,
          "name": "表名",
          "comment": "表注释",
          "fields": [
            {
              "name": "字段名",
              "type": "字段类型",
              "comment": "字段注释"
            }
          ]
        }
      ]
    }
  ],
  "message": "成功"  // 可选
}
```

## 如何匹配合适的数据源
<!-- 这是一张图片，ocr 内容为： -->
![](https://cdn.nlark.com/yuque/0/2026/png/365146/1774584792750-38573fd7-932d-4d78-9fc9-0ab66f2175fa.png)

<!-- 这是一张图片，ocr 内容为： -->
![](https://cdn.nlark.com/yuque/0/2026/png/365146/1774585392692-63b819fe-cfdf-4dae-a189-ca5982fb758e.png)

## 如果没有找到合适的数据源会如何处理：
当前会抛出异常返回

<!-- 这是一张图片，ocr 内容为： -->
![](https://cdn.nlark.com/yuque/0/2026/png/365146/1774589811514-e1a342b4-d40b-436c-ac3f-758895f0c62d.png)



## 在chat中datasource是否会切换。
在一个chat中当前如果有若干个问题则只会根据第一个问题进行数据源选择，默认情况下较为符合常理，因为用户的问题通常来说应该局部性比较强。

如果需要改造成适配可以动态切换的过程则可以在validate_history_ds的过程中将在走一遍select_datasouce的逻辑

这样做的缺点在于可能无法对历史数据的查询检索等产生影响，个人的建议是将datasocue绑定到question粒度，但是相应的改造难度比较大。可以在未来的改造过程中实现。

<!-- 这是一张图片，ocr 内容为： -->
![](https://cdn.nlark.com/yuque/0/2026/png/365146/1774586441885-0e9af8b9-d6e0-4820-af35-775df3aaff95.png)

## 如何获取到生成sql的关联表
通过template的模式，我们可以看到rule中有写入将生成sql所用到的tables返回到返回的json中。可以直接使用这个列表

<!-- 这是一张图片，ocr 内容为： -->
![](https://cdn.nlark.com/yuque/0/2026/png/365146/1774589233241-f758e9c5-548e-4f45-adbe-5b7efb230729.png)

# 变更逻辑
1. 在Data服务中提供接口
    1. 一个所有数据源及相关表的数据结构。仅仅包括当前的已上线的表/和对应的目录信息。
    2. 单个用户是否有相关表的接口
2. 在middleware中通过token进行Assistant的初始化，并在system_assisatent中写入相关配置
3. 根据现有逻辑会根据Assistant的信息初始化数据源。
4. 在数据sql的逻辑中，在generate_sql的方法返回之前，调用Data模块确认权限信息
    1. 如果权限信息为无则通过yield 返回sse给前端，返回信息包括这表的名称和目录信息。
5. 改造一下当前的select_datasource，在没有找到数据源的情况下，使用sse将返回并询问是否需要录入相关问题，如果选择是则调用后端接口记录相关信息。同时前端根据该信息终止回话。

