"""Trusted server-console bootstrap. No default administrator password."""
import argparse
import app
import management

def main():
    parser=argparse.ArgumentParser(description='최초 수퍼관리자 지정: 먼저 성과혁신실 또는 AI정보융합실 소속으로 회원가입하세요.')
    parser.add_argument('command',choices=['bootstrap-superadmin'])
    parser.add_argument('--employee-id',required=True)
    parser.add_argument('--db',type=app.Path,default=app.DB)
    args=parser.parse_args()
    app.DB=args.db
    app.initialize()
    with app.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute("SELECT 1 FROM users WHERE role='superadmin'").fetchone():
            parser.error('수퍼관리자가 이미 있습니다. 웹의 권한 관리에서 추가 지정해 주세요.')
        user=db.execute('SELECT * FROM users WHERE employee_id=?',(args.employee_id,)).fetchone()
        if not user or not management.can_be_superadmin(user['department_id'], app.CATALOG):
            parser.error('성과혁신실 또는 AI정보융합실 소속으로 가입한 계정을 지정해 주세요.')
        db.execute("UPDATE users SET role='superadmin',admin_department_id=NULL WHERE employee_id=?",(args.employee_id,))
        db.execute('DELETE FROM sessions WHERE employee_id=?',(args.employee_id,))
        management.audit(db,'SERVER-CONSOLE','최초 수퍼관리자 지정',None,args.employee_id,
            {'role':user['role']},{'role':'superadmin'},'서버 운영자 콘솔에서 초기 지정')
    print('수퍼관리자 지정 완료. 해당 계정으로 다시 로그인하세요.')

if __name__=='__main__':main()
