import java.nio.file.*;
import java.nio.charset.StandardCharsets;
import java.sql.*;
import java.util.*;
import java.util.regex.*;
import java.security.SecureRandom;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;

/** Guarded bootstrap: new SQLBot test users only, no administrator role. */
public class CreateSqlbotAccounts {
    static Connection db;
    static long scalar(String sql, Object... args) throws Exception {
        try (PreparedStatement s=db.prepareStatement(sql)) {
            for(int i=0;i<args.length;i++) s.setObject(i+1,args[i]);
            try(ResultSet r=s.executeQuery()) {
                if(!r.next()) throw new IllegalStateException("Missing expected row");
                long result=r.getLong(1);
                if(r.next()) throw new IllegalStateException("Nonunique result");
                return result;
            }
        }
    }
    static void insert(String sql,Object...args) throws Exception {
        try(PreparedStatement s=db.prepareStatement(sql)) {
            for(int i=0;i<args.length;i++)s.setObject(i+1,args[i]);
            if(s.executeUpdate()!=1)throw new IllegalStateException("Expected one inserted row");
        }
    }
    public static void main(String[] args) throws Exception {
        boolean restricted = args.length > 2 && "--restricted".equals(args[2]);
        Path output=Paths.get(args[1]);
        if(Files.exists(output))throw new IllegalStateException("Account output already exists");
        String config=new String(Files.readAllBytes(Paths.get(args[0])),StandardCharsets.UTF_8);
        Matcher m=Pattern.compile("(?m)^ +url: (jdbc:dm:[^\\r\\n]+)\\r?\\n +username: ([^\\r\\n]+)\\r?\\n +password: ([^\\r\\n]+)").matcher(config);
        if(!m.find())throw new IllegalStateException("DM connection not found");
        Class.forName("dm.jdbc.driver.DmDriver");
        db=DriverManager.getConnection(m.group(1).trim(),m.group(2).trim(),m.group(3).trim());
        db.setAutoCommit(false);
        try {
            if(scalar("SELECT ID FROM MQB.SYSTEM_TENANT WHERE NAME='八院' AND STATUS=0 AND DELETED=0 FOR UPDATE")!=124)throw new IllegalStateException("Tenant mismatch");
            if(scalar(restricted ? "SELECT COUNT(*) FROM MQB.SYSTEM_USERS WHERE TENANT_ID=124 AND USERNAME='sqlbot4'" : "SELECT COUNT(*) FROM MQB.SYSTEM_USERS WHERE TENANT_ID=124 AND USERNAME IN ('sqlbot1','sqlbot2','sqlbot3')")!=0)throw new IllegalStateException("Existing account; do not overwrite");
            if(scalar(restricted ? "SELECT COUNT(*) FROM MQB.SYSTEM_ROLE WHERE TENANT_ID=124 AND CODE='jt_sqlbot_restricted'" : "SELECT COUNT(*) FROM MQB.SYSTEM_ROLE WHERE TENANT_ID=124 AND CODE IN ('jt_sqlbot_reader','jt_sqlbot_denied','jt_sqlbot_indexer')")!=0)throw new IllegalStateException("Existing role");
            String[] codes=restricted ? new String[]{"jt_sqlbot_restricted"} : new String[]{"jt_sqlbot_reader","jt_sqlbot_denied","jt_sqlbot_indexer"};
            String[] names=restricted ? new String[]{"SQLBot测试受限用户"} : new String[]{"SQLBot测试有权用户","SQLBot测试无权用户","SQLBot测试索引管理员"};
            StringBuilder result=new StringBuilder("tenantName=八院\ntenantId=124\n");
            for(int i=0;i<codes.length;i++) {
                String user="sqlbot"+(restricted ? 4 : i+1);
                byte[] bytes=new byte[8];new SecureRandom().nextBytes(bytes);
                StringBuilder pw=new StringBuilder();for(byte b:bytes)pw.append(String.format("%02x",b));
                insert("INSERT INTO MQB.SYSTEM_ROLE (NAME,CODE,SORT,DATA_SCOPE,STATUS,TYPE,REMARK,CREATOR,CREATE_TIME,UPDATER,UPDATE_TIME,DELETED,TENANT_ID) VALUES (?,?,1,5,0,2,'jt dev-1 SQLBot测试','jt_deploy',CURRENT_TIMESTAMP,'jt_deploy',CURRENT_TIMESTAMP,0,124)",names[i],codes[i]);
                long role=scalar("SELECT ID FROM MQB.SYSTEM_ROLE WHERE TENANT_ID=124 AND CODE=? AND DELETED=0",codes[i]);
                insert("INSERT INTO MQB.SYSTEM_USERS (USERNAME,PASSWORD,NICKNAME,REMARK,SEX,STATUS,CREATOR,CREATE_TIME,UPDATER,UPDATE_TIME,DELETED,TENANT_ID) VALUES (?,?,?,'jt dev-1 SQLBot测试账号',0,0,'jt_deploy',CURRENT_TIMESTAMP,'jt_deploy',CURRENT_TIMESTAMP,0,124)",user,new BCryptPasswordEncoder().encode(pw),names[i]);
                long uid=scalar("SELECT ID FROM MQB.SYSTEM_USERS WHERE TENANT_ID=124 AND USERNAME=? AND DELETED=0",user);
                insert("INSERT INTO MQB.SYSTEM_USER_ROLE(USER_ID,ROLE_ID,CREATOR,CREATE_TIME,UPDATER,UPDATE_TIME,DELETED,TENANT_ID) VALUES (?,?,'jt_deploy',CURRENT_TIMESTAMP,'jt_deploy',CURRENT_TIMESTAMP,0,124)",uid,role);
                Set<Long> menus=new TreeSet<>(Arrays.asList(3411L,3429L,3412L,3447L));
                if(i==2)for(String permission:Arrays.asList("dmgt:data-source:query","dmgt:data-source:sync")) menus.add(scalar("SELECT MIN(ID) FROM MQB.SYSTEM_MENU WHERE PERMISSION=? AND DELETED=0 AND STATUS=0",permission));
                for(long menu:new ArrayList<>(menus)) {
                    long parent=scalar("SELECT PARENT_ID FROM MQB.SYSTEM_MENU WHERE ID=? AND DELETED=0 AND STATUS=0",menu);
                    while(parent>0 && menus.add(parent))parent=scalar("SELECT PARENT_ID FROM MQB.SYSTEM_MENU WHERE ID=? AND DELETED=0",parent);
                }
                for(long menu:menus)insert("INSERT INTO MQB.SYSTEM_ROLE_MENU(ROLE_ID,MENU_ID,CREATOR,CREATE_TIME,UPDATER,UPDATE_TIME,DELETED,TENANT_ID) VALUES (?,?,'jt_deploy',CURRENT_TIMESTAMP,'jt_deploy',CURRENT_TIMESTAMP,0,124)",role,menu);
                result.append(user).append(".id=").append(uid).append('\n').append(user).append(".password=").append(pw).append('\n');
            }
            Files.write(output,result.toString().getBytes(StandardCharsets.UTF_8),StandardOpenOption.CREATE_NEW);
            db.commit();
            System.out.println("SQLBOT_TEST_ACCOUNTS_CREATED tenant=124 count="+codes.length);
        }catch(Exception e){db.rollback();throw e;}finally{db.close();}
    }
}
